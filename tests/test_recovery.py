"""Crash-boundary, ownership and conservative-accounting tests without runtime dependencies."""
import asyncio
import hashlib
import sqlite3

import pytest

from runtime.admission import AdmissionController, AdmissionPolicy, AdmissionRejected
from runtime.containerd import ContainerdConfig
from runtime.contracts import GatewayEndpoint, ResourceRequests, RuntimeErrorBase, RuntimeSpec, RuntimeHandle, RuntimeState
from runtime.registry import RuntimeRegistry
from runtime.recovery import RecoveringRuntimeSupervisor
from test_admission import Detector, Client

IMAGE = 'example/gateway@sha256:' + 'a' * 64
IDENTITY = 'europa-' + 'a' * 32
RESOURCES = ResourceRequests(.5, 512 * 1024**2, 128)
CONFIG = ContainerdConfig(IMAGE)
SCOPE = {'socket': '/test/socket', 'namespace': 'europa'}


def registry(tmp_path):
    directory = tmp_path / 'private'
    directory.mkdir(mode=0o700, exist_ok=True)
    return RuntimeRegistry(directory / 'runtime.sqlite3', SCOPE)


def seed(journal, name='A', identity=IDENTITY, phase='active'):
    journal.create(name, identity, 'standard', RESOURCES, CONFIG)
    journal.prepare_start(identity, 1, 'test-token')
    journal.transition(identity, phase)
    return journal.get(identity)


def test_registry_transactions_constraints_and_restart(tmp_path):
    journal = registry(tmp_path)
    owner = journal.owner_id
    seed(journal)
    with pytest.raises(sqlite3.IntegrityError):
        journal.create('A', 'europa-' + 'b' * 32, None, RESOURCES, CONFIG)
    with pytest.raises(RuntimeError):
        with journal.transaction():
            journal.db.execute("UPDATE allocations SET phase='deleting'")
            raise RuntimeError('interrupted transaction')
    assert journal.get(IDENTITY)['phase'] == 'active'
    assert journal.db.execute('PRAGMA synchronous').fetchone()[0] == 2
    assert journal.db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    journal.close()
    journal = registry(tmp_path)
    assert journal.owner_id == owner and journal.get(IDENTITY)['generation'] == 1
    assert journal.get(IDENTITY)['token_hash'] == hashlib.sha256(b'test-token').hexdigest()
    assert b'test-token' not in journal.path.read_bytes()
    journal.transition(IDENTITY, 'deleting')
    journal.transition(IDENTITY, 'destroyed')
    journal.transition(IDENTITY, 'destroyed')
    with pytest.raises(RuntimeErrorBase): journal.transition(IDENTITY, 'active')
    journal.create('A', 'europa-' + 'c' * 32, None, RESOURCES, CONFIG)
    journal.close()


def test_registry_exclusion_scope_and_permissions(tmp_path):
    journal = registry(tmp_path)
    with pytest.raises(AdmissionRejected): registry(tmp_path)
    path = journal.path
    journal.close()
    with pytest.raises(RuntimeErrorBase, match='scope'):
        RuntimeRegistry(path, {'namespace': 'other'})
    path.chmod(0o644)
    with pytest.raises(RuntimeErrorBase, match='private'):
        RuntimeRegistry(path, SCOPE)
    path.chmod(0o600)
    journal = registry(tmp_path)
    journal.close()
    path.unlink()
    target = tmp_path / 'other'
    target.write_text('untouched')
    path.symlink_to(target)
    with pytest.raises(OSError): registry(tmp_path)
    assert target.read_text() == 'untouched'


class Provider:
    def __init__(self):
        self.registry = None
        self.config = CONFIG
        self.fail_inventory = self.fail_recover = self.fail_destroy = False
        self.objects = {}
        self.records = {}
        self.destroyed = []
        self.backend = self
        self.limit_mismatch = False

    def set_session(self, epoch): pass

    async def recovery_inventory(self, **kwargs):
        if self.fail_inventory: raise RuntimeErrorBase('unavailable')
        return {'containers': list(self.objects.values()), 'tasks': list(self.objects),
                'snapshots': [], 'state_directories': []}

    def put(self, row):
        self.objects[row['runtime_id']] = {'id': row['runtime_id'], 'image': row['image'], 'handler': row['handler'],
            'labels': {'europa.runtime_id': row['runtime_id'], 'europa.notebook_id': row['notebook_id'],
                       'europa.owner_id': row['owner_id']}, 'resources': vars(RESOURCES)}

    def restore_record(self, row):
        self.records[row['runtime_id']] = row

    async def recover(self, row):
        if self.fail_recover: raise RuntimeErrorBase('unready')
        self.restore_record(row)
        return RuntimeHandle(row['runtime_id'], 'containerd', row['notebook_id'],
                             GatewayEndpoint('http://10.77.0.2:8888', 'test-token'), row['generation'], RuntimeState.READY, 'now')

    async def destroy(self, identity):
        if self.fail_destroy: raise RuntimeErrorBase('destroy unavailable')
        self.destroyed.append(identity)
        self.objects.pop(identity, None)

    async def request(self, operation, identity, **kwargs):
        assert operation == 'inventory'
        return {'containers': int(identity in self.objects), 'tasks': int(identity in self.objects),
                'snapshot_present': False, 'network_state_present': False}


def supervisor(journal, provider, monkeypatch):
    class RecoveryClient(Client):
        async def recover_kernels(self): return []
        async def disconnect(self): pass
    monkeypatch.setattr('runtime.recovery.GatewayClient', RecoveryClient)
    admission = AdmissionController(Detector(cpu=.5), policy=AdmissionPolicy(memory_reserve=0, cpu_reserve=0))
    return RecoveringRuntimeSupervisor(provider, admission, journal)


async def test_recover_restores_reservations_deduplicates_and_detaches(tmp_path, monkeypatch):
    journal = registry(tmp_path)
    row = seed(journal)
    provider = Provider(); provider.put(row)
    control = supervisor(journal, provider, monkeypatch)
    await control.reconcile()
    assert control.notebooks == {'A': IDENTITY}
    assert (await control.admission.metrics())['reserved']['cpu_cores'] == .5
    assert journal.get(IDENTITY)['phase'] == 'active'
    with pytest.raises(AdmissionRejected):
        await control.admission.reserve('B', RuntimeSpec('B'))
    await control.close()
    assert not provider.destroyed and journal.get(IDENTITY)['phase'] == 'active'
    await control.destroy_notebook('A')
    assert (await control.admission.metrics())['reservation_count'] == 0
    assert journal.get(IDENTITY)['phase'] == 'destroyed'
    await control.destroy_notebook('A')
    journal.close()


@pytest.mark.parametrize('fault', ['unknown', 'owner', 'notebook', 'resources', 'image', 'handler', 'unavailable'])
async def test_inconsistent_live_inventory_blocks_admission_without_deletion(tmp_path, monkeypatch, fault):
    journal = registry(tmp_path)
    row = seed(journal)
    provider = Provider(); provider.put(row)
    obj = provider.objects[IDENTITY]
    if fault == 'unknown': provider.objects['europa-' + 'f' * 32] = dict(obj, id='europa-' + 'f' * 32)
    if fault in {'owner', 'notebook'}: obj['labels']['europa.' + ('owner_id' if fault == 'owner' else 'notebook_id')] = 'different'
    if fault == 'resources': obj['resources'] = vars(ResourceRequests(1, 512 * 1024**2, 128))
    if fault == 'image': obj['image'] = 'different'
    if fault == 'handler': obj['handler'] = 'runsc'
    if fault == 'unavailable': provider.fail_inventory = True
    control = supervisor(journal, provider, monkeypatch)
    await control.reconcile()
    assert (await control.recovery_status())['new_admissions_blocked']
    assert (await control.admission.metrics())['reserved']['cpu_cores'] == .5
    with pytest.raises(AdmissionRejected): await control.client_for('B')
    if fault != 'unknown':
        with pytest.raises(RuntimeErrorBase): await control.destroy_notebook('A')
    await control.close()
    assert not provider.destroyed
    journal.close()


async def test_missing_runtime_releases_only_after_complete_inventory(tmp_path, monkeypatch):
    journal = registry(tmp_path); seed(journal)
    provider = Provider(); provider.fail_inventory = True
    control = supervisor(journal, provider, monkeypatch)
    await control.reconcile()
    assert (await control.admission.metrics())['reservation_count'] == 1
    await control.close()
    provider.fail_inventory = False
    control = supervisor(journal, provider, monkeypatch)
    await control.reconcile()
    assert (await control.admission.metrics())['reservation_count'] == 0
    assert journal.get(IDENTITY)['phase'] == 'destroyed'
    assert not provider.destroyed
    journal.close()


@pytest.mark.parametrize('phase', ['provisioning', 'active', 'deleting'])
async def test_crash_boundaries_and_interrupted_cleanup(tmp_path, monkeypatch, phase):
    journal = registry(tmp_path); row = seed(journal, phase=phase)
    provider = Provider(); provider.put(row)
    control = supervisor(journal, provider, monkeypatch)
    await control.reconcile()
    if phase == 'deleting':
        assert journal.get(IDENTITY)['phase'] == 'destroyed' and provider.destroyed == [IDENTITY]
        assert (await control.admission.metrics())['reservation_count'] == 0
    else:
        assert control.notebooks['A'] == IDENTITY
        await control.destroy_notebook('A')
    journal.close()


async def test_crashed_or_unverifiable_gateway_retains_then_explicit_cleanup(tmp_path, monkeypatch):
    journal = registry(tmp_path); row = seed(journal)
    provider = Provider(); provider.put(row); provider.fail_recover = True
    control = supervisor(journal, provider, monkeypatch)
    await control.reconcile()
    assert not control.notebooks and control.reconciliation_notes['A']
    assert journal.get(IDENTITY)['phase'] == 'reconciliation'
    assert (await control.admission.metrics())['reservation_count'] == 1
    provider.fail_destroy = True
    with pytest.raises(RuntimeErrorBase): await control.destroy_notebook('A')
    assert journal.get(IDENTITY)['phase'] == 'deleting'
    assert (await control.admission.metrics())['reservation_count'] == 1
    provider.fail_destroy = False
    await control.destroy_notebook('A')
    assert journal.get(IDENTITY)['phase'] == 'destroyed'
    journal.close()


async def test_sqlite_process_kill_rolls_back_uncommitted_transition(tmp_path):
    import sys
    journal = registry(tmp_path); seed(journal)
    path = journal.path
    journal.close()
    checkpoint = tmp_path / 'transaction-open'
    source = '''import sqlite3, sys
from pathlib import Path
connection=sqlite3.connect(sys.argv[1])
connection.execute('BEGIN IMMEDIATE')
connection.execute("UPDATE allocations SET phase='deleting'")
Path(sys.argv[2]).write_text('uncommitted')
sys.stdin.buffer.read()
'''
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', source, str(path), str(checkpoint),
                                                  stdin=asyncio.subprocess.PIPE)
    try:
        async with asyncio.timeout(5):
            while not checkpoint.exists(): await asyncio.sleep(.01)
    finally:
        if process.returncode is None: process.kill()
        await process.wait()
    journal = registry(tmp_path)
    assert journal.get(IDENTITY)['phase'] == 'active'
    assert journal.db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    journal.close()


async def test_failed_journal_write_precedes_every_container_side_effect(tmp_path, monkeypatch):
    from test_containerd_provider import provider
    runtime, backend = provider(monkeypatch)
    backend.set_session = lambda epoch: None
    journal = registry(tmp_path)
    control = RecoveringRuntimeSupervisor(runtime, AdmissionController(Detector()), journal)
    control.reconciled = True
    def fail(*args): raise sqlite3.OperationalError('disk full')
    monkeypatch.setattr(journal, 'create', fail)
    with pytest.raises(sqlite3.OperationalError): await control.client_for('A')
    assert not backend.calls and not runtime._runtimes
    assert (await control.admission.metrics())['reservation_count'] == 0
    journal.close()


@pytest.mark.parametrize('inventory', [None, {}, {'containers': [], 'snapshots': [], 'tasks': 'not-a-list', 'state_directories': []},
    {'containers': [], 'snapshots': [], 'tasks': [], 'state_directories': None}])
async def test_incomplete_inventory_is_not_evidence_of_absence(tmp_path, monkeypatch, inventory):
    journal = registry(tmp_path); seed(journal)
    provider = Provider()
    async def invalid(**kwargs): return inventory
    provider.recovery_inventory = invalid
    control = supervisor(journal, provider, monkeypatch)
    await control.reconcile()
    assert control.recovery_blockers
    assert journal.get(IDENTITY)['phase'] == 'active'
    assert (await control.admission.metrics())['reservation_count'] == 1
    journal.close()
