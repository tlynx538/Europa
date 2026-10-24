"""Bounded pool lifecycle and durable assignment tests without process isolation claims."""
import asyncio
from dataclasses import asdict
import uuid

import pytest

from kernel.gateway_client import GatewayClient
from runtime.admission import AdmissionController, AdmissionPolicy, AdmissionRejected, ResourceProfiles
from runtime.contracts import GatewayEndpoint, ResourceRequests, RuntimeHandle, RuntimeSpec, RuntimeState, RuntimeStatus, RuntimeErrorBase
from runtime.prewarming import PrewarmingRuntimeSupervisor
from runtime.registry import RuntimeRegistry
from test_admission import Detector
from test_recovery import CONFIG, SCOPE


SERVICES = {}


class Client(GatewayClient):
    block_kernel = None
    kernel_entered = None

    async def __aenter__(self): return self
    async def wait_ready(self, kernel): return {}
    async def _request(self, method, path, **kwargs):
        service = SERVICES[self.base_url]
        if path == '/api/kernelspecs': return {'kernelspecs': {'python3': {}}}
        if method == 'GET' and path == '/api/kernels': return list(service.values())
        if method == 'POST':
            model = {'id': uuid.uuid4().hex, 'name': 'python3', 'execution_state': 'idle'}
            service[model['id']] = model
            if self.kernel_entered is not None: self.kernel_entered.set()
            if self.block_kernel is not None: await self.block_kernel.wait()
            return model
        if method == 'DELETE': service.pop(path.rsplit('/', 1)[1], None); return None
        return service[path.rsplit('/', 1)[1]]


class Provider:
    def __init__(self):
        self.registry = None
        self.config = CONFIG
        self.backend = self
        self.objects = {}
        self.handles = {}
        self.fail_destroy = False
        self.fail_recover = False
        self.pause = None
        self.started = asyncio.Event()
        self.creations = 0

    def set_session(self, epoch): pass
    async def recovery_inventory(self, **kwargs):
        return {'containers': list(self.objects.values()), 'tasks': list(self.objects), 'snapshots': [], 'state_directories': []}
    async def provision(self, spec):
        identity = spec.allocation_id
        self.creations += 1
        self.registry.prepare_start(identity, 1, 'fake-token')
        row = self.registry.get(identity)
        self.objects[identity] = {'id': identity, 'image': row['image'], 'handler': row['handler'],
            'resources': asdict(spec.resources), 'labels': {'europa.runtime_id': identity,
                'europa.notebook_id': row['allocation_key'], 'europa.owner_id': row['owner_id']}}
        endpoint = GatewayEndpoint('http://' + identity + ':8888', 'fake-token')
        SERVICES[endpoint.url] = {}
        self.handles[identity] = RuntimeHandle(identity, 'containerd', spec.notebook_id, endpoint, 1, RuntimeState.READY, 'now')
        self.started.set()
        if self.pause is not None: await self.pause.wait()
        return self.handles[identity]
    def restore_record(self, row, endpoint=None):
        name = row['notebook_id'] or row['allocation_key']
        handle = RuntimeHandle(row['runtime_id'], 'containerd', name, endpoint, row['generation'],
            RuntimeState.READY if endpoint else RuntimeState.FAILED, 'now')
        self.handles[handle.runtime_id] = handle
        return handle
    async def recover(self, row):
        if self.fail_recover: raise RuntimeErrorBase('not ready')
        identity = row['runtime_id']
        return self.restore_record(row, GatewayEndpoint('http://' + identity + ':8888', 'fake-token'))
    async def verify_clean_workspace(self, row): pass
    async def inspect(self, identity):
        return RuntimeStatus(self.handles[identity], True, True, resource_limits_enforced=True)
    async def destroy(self, identity):
        if self.fail_destroy: raise RuntimeErrorBase('unverified cleanup')
        self.objects.pop(identity, None)
        SERVICES.pop('http://' + identity + ':8888', None)
    async def request(self, operation, identity, **kwargs):
        assert operation == 'inventory'
        return {'containers': int(identity in self.objects), 'tasks': int(identity in self.objects),
                'snapshot_present': False, 'network_state_present': False}


def control(tmp_path, monkeypatch, *, provider=None, target=1, cpu=1):
    monkeypatch.setattr('runtime.prewarming.GatewayClient', Client)
    monkeypatch.setattr('runtime.recovery.GatewayClient', Client)
    monkeypatch.setattr('runtime.supervisor.GatewayClient', Client)
    directory = tmp_path / 'registry'
    directory.mkdir(mode=0o700, exist_ok=True)
    registry = RuntimeRegistry(directory / 'runtime.sqlite3', SCOPE)
    admission = AdmissionController(Detector(), policy=AdmissionPolicy(cpu_ceiling=cpu))
    return PrewarmingRuntimeSupervisor(provider or Provider(), admission, registry, target=target)


async def cleanup(s):
    await s.pool.stop()
    for row in list(s.registry.records()):
        await s.destroy_notebook(s.registry.key(row))
    await s.close()
    assert (await s.admission.metrics())['reservation_count'] == 0
    assert not s.providers['containerd'].objects
    s.registry.close()


async def fill(s):
    s.pool.schedule()
    await s.pool.wait()


async def test_ready_warm_has_no_notebook_or_kernel_and_full_reservation(tmp_path, monkeypatch):
    s = control(tmp_path, monkeypatch)
    await s.reconcile(); await fill(s)
    row = s.pool.rows()[0]
    assert row['phase'] == 'ready' and row['notebook_id'] is None and row['kind'] == 'warm'
    assert await s._clients[row['runtime_id']].list_kernels() == []
    metrics = await s.admission.metrics()
    assert metrics['reserved']['cpu_cores'] == .5 and metrics['active_notebook_runtime_count'] == 0
    assert (await s.admission.reservation(s.registry.key(row)))['phase'] == 'warm_ready'
    await cleanup(s)


async def test_claim_bootstrap_and_replenishment_never_recycle(tmp_path, monkeypatch):
    s = control(tmp_path, monkeypatch)
    await s.reconcile(); await fill(s)
    original = s.pool.rows()[0]
    client = await s.client_for('A')
    assert s.notebooks['A'] == original['runtime_id']
    model = await client.create_kernel()
    assert len(await client.list_kernels()) == 1
    assert s.registry.get(original['runtime_id'])['initial_kernel_id'] is None
    assert s.registry.get(original['runtime_id'])['allocation_key'] == original['allocation_key']
    await s.pool.wait()
    assert s.pool.rows()[0]['runtime_id'] != original['runtime_id']
    with pytest.raises(RuntimeErrorBase): s.registry.transition(original['runtime_id'], 'ready')
    await s.destroy_notebook('A'); await s.pool.wait()
    assert s.registry.get(original['runtime_id'])['phase'] == 'destroyed'
    await cleanup(s)


async def test_concurrent_duplicate_and_distinct_claims_are_exclusive(tmp_path, monkeypatch):
    s = control(tmp_path, monkeypatch)
    await s.reconcile(); await fill(s)
    await asyncio.gather(*(s.client_for('A') for _ in range(12)), s.client_for('B'))
    assert s.notebooks['A'] != s.notebooks['B']
    assigned = [row for row in s.registry.records() if row['kind'] == 'assigned']
    assert len(assigned) == 2 and {row['notebook_id'] for row in assigned} == {'A', 'B'}
    await s.pool.wait()
    assert not s.pool.rows()  # Two full active reservations exhaust the configured CPU budget.
    assert (await s.admission.metrics())['reserved']['cpu_cores'] == 1
    with pytest.raises(AdmissionRejected): await s.client_for('excess')
    await cleanup(s)


@pytest.mark.parametrize('target,cpu,ready', [(0, 1, 0), (1, .25, 0), (1, .5, 1)])
async def test_disabled_and_capacity_bounded_pool(tmp_path, monkeypatch, target, cpu, ready):
    s = control(tmp_path, monkeypatch, target=target, cpu=cpu)
    await s.reconcile(); await fill(s)
    assert s.pool.status()['ready'] == ready
    if cpu == .25:
        assert 'CPU' in s.pool.last_error
        assert s.providers['containerd'].creations == 0
    await cleanup(s)


async def test_contaminated_warm_is_destroyed_before_cold_fallback(tmp_path, monkeypatch):
    s = control(tmp_path, monkeypatch)
    await s.reconcile(); await fill(s)
    row = s.pool.rows()[0]
    client = s._clients[row['runtime_id']]
    SERVICES[client.base_url]['foreign'] = {'id': 'foreign', 'name': 'python3', 'execution_state': 'idle'}
    await s.client_for('A')
    assert s.notebooks['A'] != row['runtime_id']
    assert s.registry.get(row['runtime_id'])['phase'] == 'destroyed'
    await cleanup(s)


async def test_failed_warm_cleanup_retains_reservation_and_blocks_admission(tmp_path, monkeypatch):
    s = control(tmp_path, monkeypatch)
    await s.reconcile(); await fill(s)
    provider = s.providers['containerd']
    provider.fail_recover = provider.fail_destroy = True
    with pytest.raises(RuntimeErrorBase): await s.client_for('A')
    assert s.recovery_blockers and (await s.admission.metrics())['reservation_count'] == 1
    with pytest.raises(AdmissionRejected): await s.client_for('B')
    provider.fail_recover = provider.fail_destroy = False
    await cleanup(s)


async def test_cancel_replenishment_cleans_partial_allocation(tmp_path, monkeypatch):
    provider = Provider(); provider.pause = asyncio.Event()
    s = control(tmp_path, monkeypatch, provider=provider)
    await s.reconcile()
    s.pool.schedule(); await provider.started.wait()
    await s.pool.stop()
    assert not provider.objects and not s.registry.records()
    assert (await s.admission.metrics())['reservation_count'] == 0
    await cleanup(s)


async def test_cancel_claim_never_returns_container_to_pool(tmp_path, monkeypatch):
    s = control(tmp_path, monkeypatch)
    await s.reconcile(); await fill(s)
    identity = s.pool.rows()[0]['runtime_id']
    monkeypatch.setattr(Client, 'kernel_entered', asyncio.Event())
    monkeypatch.setattr(Client, 'block_kernel', asyncio.Event())
    task = asyncio.create_task(s.client_for('A'))
    await Client.kernel_entered.wait()
    assert s.registry.get(identity)['phase'] == 'claimed'
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert s.registry.get(identity)['phase'] == 'destroyed'
    assert identity not in {row['runtime_id'] for row in s.pool.rows()}
    await cleanup(s)


@pytest.mark.parametrize('boundary', ['ready', 'provisioning', 'claimed', 'active'])
async def test_restart_warm_and_assignment_boundaries(tmp_path, monkeypatch, boundary):
    provider = Provider()
    s = control(tmp_path, monkeypatch, provider=provider)
    await s.reconcile(); await fill(s)
    identity = s.pool.rows()[0]['runtime_id']
    if boundary == 'claimed': s.registry.claim(identity, 'A')  # Crash before in-memory reservation transfer.
    if boundary == 'provisioning': s.registry.transition(identity, 'provisioning')
    if boundary == 'active': await s.client_for('A')
    await s.close(); s.registry.close()
    recovered = control(tmp_path, monkeypatch, provider=provider, target=1)
    await recovered.reconcile()
    if boundary in {'ready', 'provisioning'}:
        assert recovered.pool.status()['ready'] == 1
        assert (await recovered.admission.metrics())['active_notebook_runtime_count'] == 0
    elif boundary == 'claimed':
        assert recovered.reconciliation_notes['A'] and not recovered.pool.rows()
        assert (await recovered.admission.reservation('A'))['resources']['cpu_cores'] == .5
    else:
        client = await recovered.client_for('A')
        assert (await client.create_kernel())['id'] in client._owned
        assert recovered.notebooks['A'] == identity
    await cleanup(recovered)


def legacy_database(tmp_path, *, invalid=False):
    import sqlite3
    import json
    import os
    directory = tmp_path / 'legacy'; directory.mkdir(mode=0o700)
    path = directory / 'runtime.sqlite3'
    connection = sqlite3.connect(path)
    connection.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    connection.executemany('INSERT INTO metadata VALUES (?,?)',
        [('version','1'), ('scope',json.dumps(SCOPE,sort_keys=True)), ('owner_id','a'*32)])
    connection.execute('''CREATE TABLE allocations(runtime_id TEXT PRIMARY KEY,notebook_id TEXT NOT NULL,
        owner_id TEXT NOT NULL,provider TEXT NOT NULL,profile_id TEXT,cpu REAL,memory INTEGER,pids INTEGER,
        image TEXT,handler TEXT,generation INTEGER,token_hash TEXT,phase TEXT,reason TEXT,created_at REAL,updated_at REAL)''')
    connection.execute("CREATE UNIQUE INDEX live_notebook ON allocations(notebook_id) WHERE phase!='destroyed'")
    connection.execute('INSERT INTO allocations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        ('europa-'+'a'*32,'A','a'*32,'containerd','standard',0 if invalid else .5,512*1024**2,128,
         CONFIG.image,'runc',1,'b'*64,'active',None,1.,1.))
    connection.commit(); connection.close(); os.chmod(path,0o600)
    return path


def test_version_one_registry_migrates_ownership_without_importing_warm_state(tmp_path):
    path = legacy_database(tmp_path)
    registry = RuntimeRegistry(path, SCOPE)
    row = registry.records()[0]
    assert row['notebook_id'] == 'A' and row['allocation_key'] == 'A'
    assert row['kind'] == 'assigned' and row['phase'] == 'active' and row['generation'] == 1
    assert row['owner_id'] == 'a'*32 and row['token_hash'] == 'b'*64
    assert row['initial_kernel_id'] is None
    assert registry.db.execute("SELECT value FROM metadata WHERE key='version'").fetchone()[0] == '2'
    registry.close()


def test_failed_schema_migration_rolls_back_original_table_and_version(tmp_path):
    import sqlite3
    path = legacy_database(tmp_path, invalid=True)
    with pytest.raises(sqlite3.IntegrityError): RuntimeRegistry(path,SCOPE)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value FROM metadata WHERE key='version'").fetchone()[0] == '1'
        assert 'allocation_key' not in {row[1] for row in connection.execute('PRAGMA table_info(allocations)')}
        assert connection.execute('SELECT notebook_id FROM allocations').fetchone()[0] == 'A'


async def test_profile_or_locale_mismatch_uses_cold_path(tmp_path, monkeypatch):
    s = control(tmp_path, monkeypatch, cpu=2)
    await s.reconcile(); await fill(s)
    identity = s.pool.rows()[0]['runtime_id']
    await s.ensure(RuntimeSpec('A', provider='containerd', environment={'LANG':'C.UTF-8'}))
    assert s.notebooks['A'] != identity and s.registry.get(identity)['kind'] == 'warm'
    await cleanup(s)


async def test_exact_profile_identity_is_required_for_warm_assignment(tmp_path, monkeypatch):
    s = control(tmp_path, monkeypatch, cpu=2)
    resources = ResourceRequests(.5,512*1024**2,128)
    s.admission.profiles = ResourceProfiles({'standard': resources, 'other': resources})
    await s.reconcile(); await fill(s)
    identity = s.pool.rows()[0]['runtime_id']
    await s.client_for('A', resource_profile='other')
    assert s.notebooks['A'] != identity and s.registry.get(identity)['kind'] == 'warm'
    await cleanup(s)


async def test_target_zero_destroys_only_unassigned_pool(tmp_path, monkeypatch):
    s = control(tmp_path, monkeypatch)
    await s.reconcile(); await fill(s)
    identity = s.pool.rows()[0]['runtime_id']
    s.pool.target = 0
    await fill(s)
    assert not s.pool.rows() and s.registry.get(identity)['phase'] == 'destroyed'
    assert (await s.admission.metrics())['reservation_count'] == 0
    await cleanup(s)


async def test_startup_gateway_claim_does_not_start_an_unsolicited_kernel(tmp_path, monkeypatch):
    s = control(tmp_path, monkeypatch)
    await s.reconcile(); await fill(s)
    s.startup_gateway_only = True
    client = await s.client_for('default')
    s.startup_gateway_only = False
    assert await client.list_kernels() == []
    assert s.registry.get(s.notebooks['default'])['initial_kernel_id'] is None
    assert (await client.create_kernel())['id']
    await cleanup(s)


async def test_disabled_restart_reclaims_warm_capacity_before_cold_start(tmp_path, monkeypatch):
    provider = Provider()
    s = control(tmp_path, monkeypatch, provider=provider, cpu=.5)
    await s.reconcile(); await fill(s)
    identity = s.pool.rows()[0]['runtime_id']
    await s.close(); s.registry.close()
    recovered = control(tmp_path, monkeypatch, provider=provider, target=0, cpu=.5)
    await recovered.reconcile()
    assert not recovered.pool.rows() and recovered.registry.get(identity)['phase'] == 'destroyed'
    await recovered.client_for('default')
    assert recovered.notebooks['default'] != identity
    await cleanup(recovered)


def test_ambiguous_durable_warm_state_is_never_reused(tmp_path):
    directory = tmp_path / 'registry'; directory.mkdir(mode=0o700)
    registry = RuntimeRegistry(directory / 'runtime.sqlite3', SCOPE)
    identity = 'europa-'+'a'*32
    registry.create(None,identity,'standard',ResourceRequests(.5,512*1024**2,128),CONFIG,warm=True)
    registry.prepare_start(identity,1,'fake-token')
    with registry.transaction(): registry.db.execute("UPDATE allocations SET phase='active'")
    with pytest.raises(RuntimeErrorBase,match='Ambiguous'): registry.records()
    registry.close()


async def test_invalid_profile_keeps_admission_rejection_accounting(tmp_path, monkeypatch):
    s = control(tmp_path, monkeypatch)
    await s.reconcile(); await fill(s)
    with pytest.raises(AdmissionRejected): await s.client_for('A',resource_profile='missing')
    assert (await s.admission.metrics())['admission_rejections'] == 1
    assert s.pool.status()['ready'] == 1 and not any(r['kind']=='assigned' for r in s.registry.records())
    await cleanup(s)
