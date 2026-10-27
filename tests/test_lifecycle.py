"""Controlled-clock activity tests: no elapsed-time sleeps or assumed remote completion."""
import asyncio
import hashlib

import pytest

from kernel.gateway_client import GatewayError
from runtime.lifecycle import IdlePolicy
from runtime.contracts import RuntimeErrorBase
from test_prewarming import control, cleanup, fill, SERVICES


class Clock:
    def __init__(self): self.now = 1000.
    def __call__(self): return self.now
    def advance(self, seconds): self.now += seconds


async def setup(tmp_path, monkeypatch, *, target=0, enabled=True, grace=5):
    s = control(tmp_path, monkeypatch, target=target)
    await s.reconcile()
    clock = Clock()
    lifecycle = s.enable_lifecycle(IdlePolicy(enabled, 60, grace, 10), clock=clock)
    client = await s.client_for('A')
    return s, client, lifecycle, clock


async def retire(lifecycle, clock):
    clock.advance(60)
    await lifecycle.sweep()
    clock.advance(5)
    await lifecycle.sweep()


async def test_idle_grace_release_and_warm_exclusion(tmp_path, monkeypatch):
    s, c, life, clock = await setup(tmp_path, monkeypatch, target=1)
    await fill(s)
    warm = s.pool.rows()[0]['runtime_id']
    identity = s.notebooks['A']
    clock.advance(60); await life.sweep()
    assert s.registry.get(identity)['phase'] == 'active'
    clock.advance(5); await life.sweep()
    assert s.registry.get(identity)['phase'] == 'destroyed'
    assert (await s.admission.metrics())['reservation_count'] == 1
    assert s.registry.get(warm)['phase'] == 'ready'
    await cleanup(s)


async def test_meaningful_activity_refreshes_but_polling_does_not(tmp_path, monkeypatch):
    s, c, life, clock = await setup(tmp_path, monkeypatch)
    clock.advance(59)
    await c.health(); await c.list_kernels(); await s.status_for('A'); life.status()
    assert life.status()['notebooks'][0]['last_activity'] == 1000
    kernel = await c.create_kernel()
    clock.advance(2); await life.sweep()
    assert 'A' in s.notebooks
    assert life.status()['notebooks'][0]['last_activity'] == 1059
    await retire(life, clock)
    assert not s.notebooks
    await cleanup(s)


async def test_disconnect_protects_execution_and_stop_records_intent(tmp_path, monkeypatch):
    s, c, life, clock = await setup(tmp_path, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    async def execute(*args, **kwargs):
        entered.set(); await release.wait(); return {'status': 'ok'}
    c.client.execute = execute
    caller = asyncio.create_task(c.execute('kernel', 'code'))
    await entered.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError): await caller
    clock.advance(1000); await life.sweep()
    assert 'A' in s.notebooks and life.status()['active_operations'] == 1
    assert life.status()['notebooks'][0]['active_executions'] == 1
    with pytest.raises(GatewayError, match='Stop recorded'): await s.destroy_notebook('A')
    with pytest.raises(RuntimeErrorBase, match='stop'): await c.create_kernel()
    owned = tuple(life.tasks)
    release.set(); await asyncio.gather(*owned)
    await life.sweep(); clock.advance(5); await life.sweep()
    assert not s.notebooks
    await cleanup(s)


@pytest.mark.parametrize('kind', ['interactive', 'agent'])
async def test_bounded_sessions_no_disconnect_or_heartbeat_inference(tmp_path, monkeypatch, kind):
    s, c, life, clock = await setup(tmp_path, monkeypatch)
    lease = await life.lease('A', kind, 300)
    saved = s.registry.db.execute('SELECT token_hash FROM leases').fetchone()[0]
    assert saved == hashlib.sha256(lease['lease_token'].encode()).hexdigest()
    clock.advance(65); await life.sweep()
    assert 'A' in s.notebooks
    # Repeated connection creation cannot extend beyond meaningful activity + 300.
    another = await life.lease('A', kind, 300)
    assert another['expires_at'] == lease['expires_at']
    clock.advance(235)
    with pytest.raises(GatewayError): await life.lease('A', kind, 300)
    await life.sweep(); clock.advance(5); await life.sweep()
    assert not s.notebooks
    await cleanup(s)


async def test_release_session_is_scoped_to_runtime_and_generation(tmp_path, monkeypatch):
    s, c, life, clock = await setup(tmp_path, monkeypatch)
    lease = await life.lease('A', 'interactive', 300)
    await s.client_for('B')
    await life.release('B', lease['lease_token'])
    assert s.registry.activity(s.notebooks['A'], clock())['leases'] == 1
    await life.release('A', lease['lease_token'])
    await retire(life, clock)
    assert not s.notebooks
    await cleanup(s)


async def test_operation_wins_candidate_and_cleanup_rejects_stale_client(tmp_path, monkeypatch):
    s, c, life, clock = await setup(tmp_path, monkeypatch)
    clock.advance(60); await life.sweep()
    await c.create_kernel()  # Synchronous journal admission cancels the pending idle candidate.
    clock.advance(5); await life.sweep()
    assert 'A' in s.notebooks
    await retire(life, clock)
    with pytest.raises(RuntimeErrorBase): await c.create_kernel()
    await cleanup(s)


async def test_cleanup_wins_concurrent_execution(tmp_path, monkeypatch):
    s, c, life, clock = await setup(tmp_path, monkeypatch, grace=0)
    provider = s.providers['containerd']
    entered, release = asyncio.Event(), asyncio.Event()
    original = provider.destroy
    async def destroy(identity):
        entered.set(); await release.wait(); await original(identity)
    provider.destroy = destroy
    clock.advance(60)
    sweep = asyncio.create_task(life.sweep()); await entered.wait()
    pending = asyncio.create_task(c.create_kernel())
    release.set(); await sweep
    with pytest.raises(RuntimeErrorBase): await pending
    assert not SERVICES.get(c.client.base_url)
    await cleanup(s)


async def test_unknown_operation_survives_restart_and_holds_capacity(tmp_path, monkeypatch):
    s, c, life, clock = await setup(tmp_path, monkeypatch)
    identity = s.notebooks['A']; row = s.registry.get(identity)
    s.registry.begin_operation(identity, row['generation'], 'execute', clock())
    clock.advance(10000); await life.sweep()
    assert 'A' in s.notebooks
    provider = s.providers['containerd']
    await s.close(); s.registry.close()
    recovered = control(tmp_path, monkeypatch, provider=provider, target=0)
    await recovered.reconcile()
    assert recovered.registry.get(identity)['phase'] == 'reconciliation'
    assert recovered.recovery_blockers and (await recovered.admission.metrics())['reservation_count'] == 1
    assert not recovered.notebooks
    await cleanup(recovered)


async def test_interrupted_idle_cleanup_resumes_after_restart(tmp_path, monkeypatch):
    s, c, life, clock = await setup(tmp_path, monkeypatch, grace=0)
    provider = s.providers['containerd']; identity = s.notebooks['A']
    provider.fail_destroy = True
    clock.advance(60); await life.sweep()
    assert s.registry.get(identity)['phase'] == 'deleting'
    assert (await s.admission.metrics())['reservation_count'] == 1
    await s.close(); s.registry.close()
    provider.fail_destroy = False
    recovered = control(tmp_path, monkeypatch, provider=provider, target=0)
    await recovered.reconcile()
    assert recovered.registry.get(identity)['phase'] == 'destroyed'
    assert (await recovered.admission.metrics())['reservation_count'] == 0
    await cleanup(recovered)


async def test_busy_gateway_and_disabled_policy_are_conservative(tmp_path, monkeypatch):
    s, c, life, clock = await setup(tmp_path, monkeypatch, enabled=False)
    await retire(life, clock)
    assert 'A' in s.notebooks
    life.policy = IdlePolicy(True, 60, 0, 10)
    SERVICES[c.client.base_url]['foreign'] = {'id': 'foreign', 'execution_state': 'busy'}
    await life.sweep(); assert 'A' in s.notebooks
    SERVICES[c.client.base_url].clear()
    await life.sweep(); assert not s.notebooks
    await cleanup(s)


@pytest.mark.parametrize('changes', [{'timeout': 0}, {'grace': -1}, {'interval': float('nan')}, {'timeout': True}, {'enabled': 1}])
def test_invalid_policy_rejected(changes):
    with pytest.raises(ValueError): IdlePolicy(**changes)


async def test_graceful_detach_does_not_abort_unfinished_remote_work(tmp_path, monkeypatch):
    s, c, life, clock = await setup(tmp_path, monkeypatch, grace=0)
    entered = asyncio.Event(); aborted = []
    async def remote(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await c.client._abort('kernel')
            raise
    async def delete(kernel): aborted.append(kernel)
    c.client.execute = remote; c.client._delete = delete
    caller = asyncio.create_task(c.execute('kernel', 'code'))
    await entered.wait()
    identity = s.notebooks['A']
    await s.close()
    with pytest.raises(asyncio.CancelledError): await caller
    assert not aborted and s.registry.unfinished(identity)
    assert s.registry.get(identity)['phase'] == 'active'
    # Explicit cleanup after shutdown remains possible for the test owner.
    await cleanup(s)


async def test_journal_failure_prevents_operation_side_effect(tmp_path, monkeypatch):
    s, c, life, clock = await setup(tmp_path, monkeypatch)
    invoked = []
    async def operation(*args, **kwargs): invoked.append(True)
    c.client.execute = operation
    original = s.registry.begin_operation
    def failed(*args): raise RuntimeErrorBase('disk failure')
    monkeypatch.setattr(s.registry, 'begin_operation', failed)
    with pytest.raises(RuntimeErrorBase): await c.execute('kernel', 'code')
    assert not invoked and not life.tasks
    monkeypatch.setattr(s.registry, 'begin_operation', original)
    await cleanup(s)


async def test_sessions_and_activity_survive_registry_reopen(tmp_path, monkeypatch):
    s, c, life, clock = await setup(tmp_path, monkeypatch)
    identity = s.notebooks['A']
    lease = await life.lease('A', 'interactive', 300)
    clock.advance(65)
    provider = s.providers['containerd']
    await s.close(); s.registry.close()
    recovered = control(tmp_path, monkeypatch, provider=provider, target=0)
    await recovered.reconcile()
    second = recovered.enable_lifecycle(IdlePolicy(True, 60, 0, 10), clock=clock)
    assert recovered.registry.activity(identity, clock())['sessions']['interactive'] == 1
    await second.sweep(); assert 'A' in recovered.notebooks
    await second.release('A', lease['lease_token']); await second.sweep()
    assert not recovered.notebooks
    await cleanup(recovered)


async def test_activity_http_and_lease_contract(tmp_path, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from internal.router import router
    s, c, life, clock = await setup(tmp_path, monkeypatch)
    app = FastAPI(); app.state.supervisor = s; app.include_router(router, prefix='/api')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://europa') as http:
        activity = (await http.get('/api/runtime/activity')).json()
        assert activity['enabled'] and activity['notebooks'][0]['active_executions'] == 0
        response = await http.post('/api/runtime/lease', params={'notebook_id': 'A', 'kind': 'interactive', 'ttl': 120})
        assert response.status_code == 201
        token = response.json()['lease_token']
        assert token not in (await http.get('/api/runtime/activity')).text
        assert (await http.delete('/api/runtime/lease', params={'notebook_id':'A'}, headers={'X-Europa-Lease': token})).status_code == 204
        assert (await http.post('/api/runtime/lease', params={'ttl':301})).status_code == 422
    await cleanup(s)


async def test_version_two_upgrade_preserves_allocations(tmp_path, monkeypatch):
    from runtime.registry import RuntimeRegistry
    from test_recovery import SCOPE
    s, c, life, clock = await setup(tmp_path, monkeypatch)
    before = s.registry.records()
    path = s.registry.path
    await s.close()
    with s.registry.transaction():
        for name in ('operations','leases','activity'): s.registry.db.execute('DROP TABLE ' + name)
        s.registry.db.execute("UPDATE metadata SET value='2' WHERE key='version'")
    s.registry.close()
    registry = RuntimeRegistry(path, SCOPE)
    assert registry.records() == before
    assert not registry.unfinished(before[0]['runtime_id'])
    assert registry.activity(before[0]['runtime_id'], clock())['last_activity'] == clock()
    registry.close()
    # Disposal through a fresh controller keeps provider/registry ownership consistent.
    recovered = control(tmp_path, monkeypatch, provider=s.providers['containerd'], target=0)
    await recovered.reconcile(); await cleanup(recovered)


async def test_rejected_operation_does_not_refresh_idle_deadline(tmp_path, monkeypatch):
    s, c, life, clock = await setup(tmp_path, monkeypatch)
    async def rejected(*args, **kwargs): raise GatewayError('not admitted', 409)
    c.client.execute = rejected
    clock.advance(59)
    with pytest.raises(GatewayError): await c.execute('kernel', 'code')
    assert life.status()['notebooks'][0]['last_activity'] == 1000
    clock.advance(1); await life.sweep(); clock.advance(5); await life.sweep()
    assert not s.notebooks
    await cleanup(s)


async def test_missing_version_three_activity_table_fails_closed(tmp_path, monkeypatch):
    from runtime.registry import RuntimeRegistry
    from test_recovery import SCOPE
    s, c, life, clock = await setup(tmp_path, monkeypatch)
    await s.close()
    path = s.registry.path
    with s.registry.transaction(): s.registry.db.execute('DROP TABLE operations')
    s.registry.close()
    with pytest.raises(RuntimeErrorBase, match='incomplete'): RuntimeRegistry(path, SCOPE)
    # Repair only this deliberately corrupted disposable test database for owned disposal.
    import sqlite3
    with sqlite3.connect(path) as db: db.execute("UPDATE metadata SET value='2' WHERE key='version'")
    recovered = control(tmp_path, monkeypatch, provider=s.providers['containerd'], target=0)
    await recovered.reconcile(); await cleanup(recovered)


async def test_shutdown_denies_new_provisioning_during_drain(tmp_path, monkeypatch):
    s, c, life, clock = await setup(tmp_path, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    original = life.close
    async def drain():
        entered.set(); await release.wait(); await original()
    monkeypatch.setattr(life, 'close', drain)
    closing = asyncio.create_task(s.close()); await entered.wait()
    with pytest.raises(RuntimeErrorBase, match='closing'): await s.client_for('B')
    assert s.providers['containerd'].creations == 1
    release.set(); await closing
    await cleanup(s)


async def test_unknown_idle_kernel_is_not_proof_of_quiescent_ownership(tmp_path, monkeypatch):
    s, c, life, clock = await setup(tmp_path, monkeypatch, grace=0)
    SERVICES[c.client.base_url]['unknown'] = {'id': 'unknown', 'execution_state': 'idle'}
    clock.advance(1000); await life.sweep()
    assert 'A' in s.notebooks and (await s.admission.metrics())['reservation_count'] == 1
    await cleanup(s)  # Explicit verified operator teardown remains available.
