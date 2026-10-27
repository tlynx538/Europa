"""Bounded real control-plane crashes in the explicitly dedicated Europa namespace."""
import asyncio
import json
import os
from pathlib import Path
import sys

import pytest
import pytest_asyncio

from runtime.admission import AdmissionController, AdmissionPolicy, AdmissionRejected, ResourceProfiles
from runtime.capacity import LinuxCapacityDetector
from runtime.contracts import ResourceRequests, RuntimeErrorBase, RuntimeSpec
from runtime.recovery import RecoveringRuntimeSupervisor
from runtime.registry import RuntimeRegistry
from test_containerd_integration import sandbox, stdout

pytestmark = pytest.mark.containerd_integration
SMALL = ResourceRequests(.25, 384 * 1024**2, 128)
EVIDENCE = []


def record_evidence(case, **observations):
    EVIDENCE.append({'case': case, **observations})
    path = Path('test-results/recovery-evidence.json')
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(EVIDENCE, indent=2) + '\n')


def admission():
    return AdmissionController(LinuxCapacityDetector(), ResourceProfiles({'standard': SMALL}),
                               AdmissionPolicy(cpu_ceiling=.5, memory_ceiling=768 * 1024**2))


async def crash_child(tmp_path, mode, monkeypatch):
    monkeypatch.setenv('EUROPA_WARM_TARGET', '0')
    monkeypatch.delenv('EUROPA_GATEWAY_URL', raising=False)
    monkeypatch.setenv('EUROPA_RUNTIME_PROVIDER', 'containerd')
    monkeypatch.setenv('EUROPA_CONTAINERD_HANDLER', 'runc')
    monkeypatch.setenv('EUROPA_RESOURCE_PROFILES', json.dumps({'standard': vars(SMALL)}))
    monkeypatch.setenv('EUROPA_ADMISSION_CPU_CAPACITY_CORES', '.5')
    monkeypatch.setenv('EUROPA_ADMISSION_MEMORY_CAPACITY_BYTES', str(768 * 1024**2))
    monkeypatch.setenv('EUROPA_RUNTIME_REGISTRY', str(tmp_path / 'registry/runtime.sqlite3'))
    path = tmp_path / 'ready.json'
    env = dict(os.environ, PYTHONPATH=str(Path('api/src').resolve()))
    log = (tmp_path / 'control-plane.log').open('wb')
    process = await asyncio.create_subprocess_exec(sys.executable, 'tests/recovery_process.py', str(path), mode,
                                                  stdout=log, stderr=log, env=env)
    try:
        async with asyncio.timeout(40):
            while not path.exists():
                if process.returncode is not None:
                    raise AssertionError('Disposable control plane exited before its checkpoint; inspect private test log')
                await asyncio.sleep(.05)
        result = json.loads(path.read_text())
        with pytest.raises(AdmissionRejected):
            RuntimeRegistry(Path(os.environ['EUROPA_RUNTIME_REGISTRY']), {})
    finally:
        if process.returncode is None:
            process.kill()  # Deliberately bypass lifespan/finally; containers must survive.
        await process.wait()
        log.close()
    assert process.returncode == -9
    return result


async def recover(provider, tmp_path):
    registry = RuntimeRegistry(tmp_path / 'registry/runtime.sqlite3', provider.recovery_scope())
    control = RecoveringRuntimeSupervisor(provider, admission(), registry)
    await control.reconcile()
    return control, registry


async def dispose(control, registry):
    # Tests explicitly destroy their own durable allocations; graceful close only detaches.
    try:
        for row in list(registry.records()):
            await control.destroy_notebook(row['notebook_id'])
        await control.close()
    finally:
        registry.close()


@pytest.mark.parametrize('sandbox', ['runc'], indirect=True)
async def test_real_abrupt_restart_two_notebooks(sandbox, tmp_path, monkeypatch):
    import copy
    import sqlite3
    provider, _ = sandbox
    await provider.assert_empty_namespace()
    before = await crash_child(tmp_path, 'serve-two', monkeypatch)
    with sqlite3.connect(tmp_path / 'registry/runtime.sqlite3') as connection:
        old_epoch = connection.execute("SELECT value FROM metadata WHERE key='session_epoch'").fetchone()[0]
    control, registry = await recover(provider, tmp_path)
    try:
        assert not control.recovery_blockers and not control.reconciliation_notes
        assert set(control.notebooks.values()) == {r['runtime_id'] for r in before['records']}
        assert (await control.admission.metrics())['reserved'] == {
            'cpu_cores': .5, 'memory_bytes': 768 * 1024**2, 'pids_limit_sum': 256}
        for name, kernel in before['kernels'].items():
            client = await control.client_for(name)
            assert stdout(await client.execute(kernel, 'print(owner_value)')) == name + '\n'
            assert (await control.status_for(name))['resource_limits_enforced']
        with pytest.raises(AdmissionRejected): await control.client_for('excess')
        assert len((await provider.recovery_inventory())['containers']) == 2
        stale = copy.copy(provider.backend)
        stale.session_epoch = old_epoch
        identity = control.notebooks['default']
        with pytest.raises(RuntimeErrorBase):
            await stale.request('destroy', identity, **provider.ownership_parameters(identity))
        assert (await provider.inspect(identity)).ready
        record_evidence('abrupt-two-notebook-restart', before=before,
                        recovered=await control.recovery_status(), admission=await control.admission.metrics(),
                        separate_kernel_values_verified=True, over_admission_rejected=True,
                        stale_session_destruction_rejected=True)
        # Duplicate registry ownership is rejected while the recovered control plane owns it.
        with pytest.raises(AdmissionRejected): RuntimeRegistry(registry.path, provider.recovery_scope())
    finally:
        await dispose(control, registry)
    await provider.assert_empty_namespace()


@pytest.mark.parametrize('sandbox', ['runc'], indirect=True)
@pytest.mark.parametrize('mode', ['created', 'started', 'deleting', 'deleted', 'missing', 'crashed'])
async def test_real_recovery_crash_boundaries(sandbox, tmp_path, monkeypatch, mode):
    provider, _ = sandbox
    await provider.assert_empty_namespace()
    before = await crash_child(tmp_path, mode if mode in {'created', 'started', 'deleting', 'deleted'} else 'serve', monkeypatch)
    identity = before['records'][0]['runtime_id']
    if mode == 'missing':
        # Simulate an operator-side deletion of this exact disposable test resource,
        # outside the killed controller's fenced durable session.
        await provider.backend.request('destroy', identity)
    if mode == 'crashed': await provider.backend.request('kill', identity)
    control, registry = await recover(provider, tmp_path)
    try:
        metrics = await control.admission.metrics()
        if mode in {'deleting', 'deleted', 'missing'}:
            assert metrics['reservation_count'] == 0
            assert registry.get(identity)['phase'] == 'destroyed'
            await provider.assert_empty_namespace()
        elif mode in {'created', 'crashed'}:
            assert metrics['reservation_count'] == 1
            assert control.reconciliation_notes['default']
            with pytest.raises(RuntimeErrorBase): await control.client_for()
        else:
            assert metrics['reservation_count'] == 1
            assert (await control.status_for('default'))['ready']
            assert registry.get(identity)['phase'] == 'active'
        record_evidence(mode, before=before, recovered=await control.recovery_status(), admission=metrics)
    finally:
        await dispose(control, registry)
    await provider.assert_empty_namespace()


@pytest.mark.parametrize('sandbox', ['runc'], indirect=True)
async def test_real_unknown_allocation_is_never_adopted_or_destroyed(sandbox, tmp_path):
    provider, _ = sandbox
    handle = await provider.provision(RuntimeSpec('test-owned-unrecorded', provider='containerd', resources=SMALL))
    # A separate provider must not adopt this test-owned, unrecorded allocation.
    from runtime.containerd import ContainerdRuntimeProvider
    observer = ContainerdRuntimeProvider(provider.backend, provider.config)
    (tmp_path / 'registry').mkdir(mode=0o700)
    control, registry = await recover(observer, tmp_path)
    try:
        assert control.recovery_blockers
        with pytest.raises(AdmissionRejected): await control.client_for()
        assert not control.notebooks and not observer._runtimes
        await control.close()
        assert (await provider.inspect(handle.runtime_id)).ready
        record_evidence('unrecorded-test-allocation', recovered=await control.recovery_status(),
                        allocation_untouched=True)
    finally:
        registry.close()
        await provider.destroy(handle.runtime_id)


@pytest.mark.parametrize('sandbox', ['runc'], indirect=True)
async def test_real_containerd_service_restart(sandbox, tmp_path, monkeypatch):
    if os.environ.get('EUROPA_TEST_CONTAINERD_RESTART') != '1':
        pytest.skip('requires explicit approval to restart the existing VM containerd service')
    provider, _ = sandbox
    await provider.assert_empty_namespace()
    before = await crash_child(tmp_path, 'serve', monkeypatch)
    identity = before['records'][0]['runtime_id']
    observation = await provider.backend.request('inspect', identity)
    service = await asyncio.create_subprocess_exec('sudo', '-n', 'systemctl', 'restart', 'containerd',
                                                  stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    assert await asyncio.wait_for(service.wait(), 30) == 0
    control, registry = await recover(provider, tmp_path)
    try:
        assert not control.recovery_blockers and not control.reconciliation_notes
        after = await provider.backend.request('inspect', identity)
        assert observation['pid'] == after['pid'] and after['state'] == 'running'
        client = await control.client_for()
        assert stdout(await client.execute(before['kernels']['default'], 'print(owner_value)')) == 'default\n'
        assert (await control.status_for('default'))['generation'] == 1
        record_evidence('containerd-service-restart', pid_before=observation['pid'], pid_after=after['pid'],
                        kernel_state_preserved=True, generation=1, recovered=await control.recovery_status())
    finally:
        await dispose(control, registry)
    await provider.assert_empty_namespace()


@pytest.mark.parametrize('sandbox', ['runc'], indirect=True)
async def test_real_graceful_restart_retains_kernel(sandbox, tmp_path, monkeypatch):
    monkeypatch.setenv('EUROPA_WARM_TARGET', '0')
    from main import create_app
    provider, _ = sandbox
    await provider.assert_empty_namespace()
    monkeypatch.delenv('EUROPA_GATEWAY_URL', raising=False)
    monkeypatch.setenv('EUROPA_RUNTIME_PROVIDER', 'containerd')
    monkeypatch.setenv('EUROPA_RUNTIME_REGISTRY', str(tmp_path / 'registry/runtime.sqlite3'))
    monkeypatch.setenv('EUROPA_RESOURCE_PROFILES', json.dumps({'standard': vars(SMALL)}))
    first = create_app()
    async with first.router.lifespan_context(first):
        control = first.state.supervisor
        identity = control.notebooks['default']
        client = await control.client_for()
        kernel = (await client.create_kernel())['id']
        assert (await client.execute(kernel, 'saved_value=42'))['status'] == 'ok'
    assert (await provider.backend.request('inspect', identity))['state'] == 'running'
    second = create_app()
    async with second.router.lifespan_context(second):
        control = second.state.supervisor
        assert control.notebooks['default'] == identity
        client = await control.client_for()
        assert stdout(await client.execute(kernel, 'print(saved_value)')) == '42\n'
        record_evidence('graceful-control-plane-restart', runtime_id=identity, kernel_id=kernel,
                        kernel_state_preserved=True, recovered=await control.recovery_status())
        await control.destroy_notebook('default')
    await provider.assert_empty_namespace()
