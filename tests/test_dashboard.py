"""Read-only projections with real document/journal fixtures and controlled Gateway evidence."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from runtime.contracts import GatewayEndpoint, RuntimeHandle, RuntimeState
from runtime.supervisor import RuntimeSupervisor
from workspace.dashboard import DashboardObservation
from test_workspace_notebooks import physical, call, associate


class Client:
    def __init__(self, state='idle'):
        self._owned = {'kernel'}
        self._busy = set()
        self._closing = False
        self.state = state
        self.calls = 0
    async def list_kernels(self):
        self.calls += 1
        return [{'id': 'kernel', 'name': 'python3', 'execution_state': self.state, 'last_activity': '2026-11-10T16:00:00Z'}]


@pytest.fixture
def dashboard(physical):
    app, root, store, journal = physical
    provider = SimpleNamespace(_runtimes={})
    supervisor = RuntimeSupervisor({'local': provider})
    app.state.supervisor = supervisor
    app.state.notebook_service = SimpleNamespace(tasks={})
    app.state.dashboard_observation = DashboardObservation(app)
    return app, root, store, journal


def seed(dashboard, title='Private'):
    app, root, store, journal = dashboard
    document = store.create(title)
    nid = document['metadata']['europa']['notebook_id']
    journal.ensure_notebook_conversation(nid)
    return nid


def live(app, nid, state='idle'):
    s = app.state.supervisor
    handle = RuntimeHandle('internal-runtime', 'local', nid, GatewayEndpoint('http://127.0.0.1:1', 'private-token'), 1, RuntimeState.READY, '2026-11-10T00:00:00Z')
    s.handles[handle.runtime_id] = handle
    s.notebooks[nid] = handle.runtime_id
    s._generations[handle.runtime_id] = 1
    client = Client(state)
    s._clients[handle.runtime_id] = client
    return client, handle


async def sessions(app, **params):
    response = await call(app, 'get', 'dashboard/sessions', params=params)
    assert response.status_code == 200, response.text
    assert 'internal-runtime' not in response.text and 'private-token' not in response.text and 'kernel_id' not in response.text
    return response.json()


@pytest.mark.anyio
async def test_recent_order_identity_locations_restart_and_missing_are_read_only(dashboard):
    app, root, store, journal = dashboard
    private = seed(dashboard)
    project = await call(app, 'post', 'workspace/projects', json={'path': 'Work', 'operation_id': 'work'})
    physical = (await associate(app, 'Work/Notes.ipynb', 'notes', True)).json()['notebook_id']
    journal.record_notebook_open(private)
    journal.record_notebook_open(physical)
    journal.record_notebook_open(private)
    before = journal.db.total_changes
    manifest = store._manifest()
    response = await call(app, 'get', 'dashboard/recent')
    assert response.status_code == 200, response.text
    items = response.json()['items']
    assert [item['id'] for item in items] == [private, physical]
    assert items[0]['location'] == 'Private catalog'
    assert items[1]['location'] == 'Home / Work'
    assert all(item['available'] for item in items)
    assert journal.db.total_changes == before and store._manifest() == manifest
    journal.close(); journal.__init__(root.parent / 'application', store)
    assert (await call(app, 'get', 'dashboard/recent')).json()['items'] == items
    document = store.open(private)
    store.delete(private, document['metadata']['europa']['revision'])
    (root / 'Work').rename(root / 'Moved')
    unavailable = (await call(app, 'get', 'dashboard/recent')).json()['items']
    assert [item['id'] for item in unavailable] == [private, physical]
    assert all(not item['available'] and item['location'] == 'Unavailable' for item in unavailable)
    assert not app.state.supervisor.notebooks


@pytest.mark.anyio
@pytest.mark.parametrize(('gateway', 'expected'), [('idle', 'Idle'), ('busy', 'Busy'), ('starting', 'Starting'), ('unrecognized', 'Unknown')])
async def test_live_status_requires_exact_owned_gateway_inventory(dashboard, gateway, expected):
    app, root, store, journal = dashboard
    nid = seed(dashboard)
    client, handle = live(app, nid, gateway)
    changes = journal.db.total_changes
    result = (await sessions(app))['items'][0]
    assert result['state'] == expected
    assert result['last_activity'] is not None
    assert (result['observed_at'] is None) == (expected == 'Unknown')
    assert journal.db.total_changes == changes
    assert client.calls == 1
    client._owned.add('missing')
    assert (await sessions(app))['items'][0]['state'] == 'Needs attention'


@pytest.mark.anyio
async def test_failures_are_isolated_no_provision_and_no_false_stopped_after_restart(dashboard):
    app, root, store, journal = dashboard
    first = seed(dashboard)
    client, handle = live(app, first)
    second = seed(dashboard, 'Never run')
    async def fail(): raise RuntimeError('do not publish this internal detail')
    client.list_kernels = fail
    data = {item['id']: item for item in (await sessions(app))['items']}
    assert data[first]['state'] == 'Unknown'
    assert data[second]['state'] == 'Stopped'
    assert 'internal detail' not in str(data)
    client.list_kernels = Client().list_kernels
    document = store.edit_cell(first, 1, 'insert', text='print(42)')
    receipt, _ = store.begin(first, document['cells'][0]['id'], 2)
    store.update_execution(first, receipt['execution_id'], runtime_id=handle.runtime_id, generation=1, kernel_id='kernel')
    store.finish(first, receipt['execution_id'], {'status': 'ok', 'outputs': [], 'execution_count': 1})
    s = app.state.supervisor
    s.notebooks.clear(); s.handles.clear(); s._clients.clear()
    assert next(item for item in (await sessions(app))['items'] if item['id'] == first)['state'] == 'Unknown'
    s.providers['local']._runtimes[handle.runtime_id] = SimpleNamespace(handle=replace(handle, state=RuntimeState.DESTROYED), process=None)
    assert next(item for item in (await sessions(app))['items'] if item['id'] == first)['state'] == 'Stopped'


@pytest.mark.anyio
async def test_active_startup_and_orphaned_uncertain_receipts_remain_conservative(dashboard):
    app, root, store, journal = dashboard
    nid = seed(dashboard)
    task = asyncio.create_task(asyncio.Event().wait())
    app.state.notebook_service.tasks[nid] = task
    try:
        assert (await sessions(app))['items'][0]['state'] == 'Starting'
        client, handle = live(app, nid)
        document = store.edit_cell(nid, 1, 'insert', text='42')
        receipt, _ = store.begin(nid, document['cells'][0]['id'], 2)
        store.update_execution(nid, receipt['execution_id'], state='running', runtime_id=handle.runtime_id, generation=1, kernel_id='kernel')
        assert (await sessions(app))['items'][0]['state'] == 'Busy'
        app.state.notebook_service.tasks.clear()
        assert (await sessions(app))['items'][0]['state'] == 'Needs attention'
        store.uncertain(nid, receipt['execution_id'])
        assert (await sessions(app))['items'][0]['state'] == 'Needs attention'
    finally:
        task.cancel(); await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
async def test_generation_race_timeout_and_coalescing(dashboard):
    app, root, store, journal = dashboard
    nid = seed(dashboard)
    client, handle = live(app, nid)
    entered, release = asyncio.Event(), asyncio.Event()
    async def wait():
        client.calls += 1
        entered.set(); await release.wait()
        return [{'id': 'kernel', 'name': 'python3', 'execution_state': 'idle'}]
    client.list_kernels = wait
    one = asyncio.create_task(sessions(app))
    await asyncio.wait_for(entered.wait(), 5)
    two = asyncio.create_task(sessions(app))
    app.state.supervisor._generations[handle.runtime_id] = 2
    release.set()
    results = await asyncio.gather(one, two)
    assert all(result['items'][0]['state'] == 'Unknown' for result in results)
    assert client.calls == 1
    app.state.supervisor._generations[handle.runtime_id] = 1
    release.clear()
    assert (await sessions(app))['items'][0]['state'] == 'Unknown'
    assert (await call(app, 'get', 'dashboard/sessions?limit=21')).status_code == 422
    assert (await call(app, 'get', 'dashboard/sessions?cursor=../../escape')).status_code == 422


@pytest.mark.anyio
async def test_registry_uncertainty_is_read_only_without_activity_initialization(dashboard, tmp_path):
    from test_recovery import registry, seed as seed_registry, IDENTITY
    app, root, store, journal = dashboard
    nid = seed(dashboard)
    client, handle = live(app, nid)
    s = app.state.supervisor
    s.registry = registry(tmp_path)
    s.reconciled = True; s.recovery_blockers = []; s.reconciliation_notes = {}
    seed_registry(s.registry, name=nid)
    try:
        handle = replace(handle, runtime_id=IDENTITY)
        s.notebooks[nid] = IDENTITY; s.handles = {IDENTITY: handle}; s._clients = {IDENTITY: client}; s._generations = {IDENTITY: 1}
        before = s.registry.db.total_changes
        assert (await sessions(app))['items'][0]['state'] == 'Idle'
        assert s.registry.db.total_changes == before
        assert s.registry.db.execute('SELECT COUNT(*) FROM activity').fetchone()[0] == 0
        s.registry.begin_operation(IDENTITY, 1, 'execute', 100)
        before = s.registry.db.total_changes
        assert (await sessions(app))['items'][0]['state'] == 'Needs attention'
        assert s.registry.db.total_changes == before
    finally:
        s.registry.close(); s.registry = None


@pytest.mark.integration
async def test_real_kernel_idle_busy_idle_stopped_and_observation_failure(dashboard, tmp_path):
    from runtime.local import LocalRuntimeProvider
    from notebooks.service import NotebookService
    app, root, store, journal = dashboard
    provider = LocalRuntimeProvider(root=tmp_path)
    s = RuntimeSupervisor({'local': provider})
    service = NotebookService(store, s)
    app.state.supervisor = s; app.state.notebook_service = service
    nid = seed(dashboard, 'Real observed notebook')
    release = tmp_path / 'release-execution'
    async def observed(label):
        async with asyncio.timeout(15):
            while True:
                item = (await sessions(app))['items'][0]
                if item['state'] == label:
                    return item
                await asyncio.sleep(0.05)  # Poll authoritative state; never infer completion from an acknowledgement.
    try:
        assert (await sessions(app))['items'][0]['state'] == 'Stopped'
        assert not provider._runtimes
        client = await s.client_for(nid)
        await client.create_kernel()
        allocation_count = len(provider._runtimes)
        idle = await observed('Idle')
        assert idle['state'] == 'Idle' and idle['last_activity'] is not None
        code = f"from pathlib import Path\nfrom threading import Event\nwhile not Path({str(release)!r}).exists():\n    Event().wait(0.05)\nprint(42)"
        document = store.edit_cell(nid, 1, 'insert', text=code)
        receipt = await service.submit(nid, document['cells'][0]['id'], 2)
        await observed('Busy')
        release.write_text('release')
        await asyncio.wait_for(asyncio.gather(*list(service.tasks.values())), 20)
        assert (await observed('Idle'))['state'] == 'Idle'
        applied = store.execution(nid, receipt['execution_id'])
        assert applied['state'] == 'applied' and applied['result']['outputs'][0]['text'] == '42\n'
        original = client.list_kernels
        async def fail(): raise RuntimeError('simulated unavailable observation')
        client.list_kernels = fail
        assert (await sessions(app))['items'][0]['state'] == 'Unknown'
        client.list_kernels = original
        assert len(provider._runtimes) == allocation_count
        saved = store.open(nid)
        await s.destroy_notebook(nid)  # Existing verified lifecycle, test infrastructure only.
        assert (await sessions(app))['items'][0]['state'] == 'Stopped'
        assert store.open(nid) == saved
    finally:
        release.touch(exist_ok=True)
        await service.close(grace=5)
        await s.close()


@pytest.mark.integration
async def test_complete_backend_lifespan_restart_keeps_recent_order(tmp_path):
    from main import create_app
    root = tmp_path / 'workspace'; root.mkdir()
    first = create_app(workspace_root=root)
    async with first.router.lifespan_context(first):
        private = (await call(first, 'post', 'notebooks', json={'title': 'Catalog recent'})).json()['metadata']['europa']['notebook_id']
        physical = (await associate(first, 'Physical.ipynb', 'create', True)).json()['notebook_id']
        for nid in (private, physical, private):
            assert (await call(first, 'post', f'notebooks/{nid}/opened')).status_code == 200
        before = (await call(first, 'get', 'dashboard/recent')).json()
        runtime_count = len(first.state.supervisor.notebooks)
        assert [item['id'] for item in before['items']] == [private, physical]
        assert len(first.state.supervisor.notebooks) == runtime_count
    second = create_app(workspace_root=root)
    async with second.router.lifespan_context(second):
        assert (await call(second, 'get', 'dashboard/recent')).json() == before
        assert len(second.state.supervisor.notebooks) == 1  # Startup Gateway only, no notebook kernel.
        assert all(item['state'] == 'Stopped' for item in (await sessions(second))['items'])


@pytest.mark.anyio
async def test_pagination_and_gateway_concurrency_are_bounded(dashboard):
    app, root, store, journal = dashboard
    s = app.state.supervisor
    entered, release = asyncio.Event(), asyncio.Event()
    active = maximum = 0
    async def inventory():
        nonlocal active, maximum
        active += 1; maximum = max(maximum, active)
        if active == 4: entered.set()
        try:
            await release.wait()
            return [{'id': 'kernel', 'name': 'python3', 'execution_state': 'idle'}]
        finally: active -= 1
    for index in range(23):
        nid = seed(dashboard, f'Notebook {index}')
        client = Client(); client.list_kernels = inventory
        handle = RuntimeHandle(f'private-{index}', 'local', nid, GatewayEndpoint('http://127.0.0.1:1', 'token'), 1, RuntimeState.READY, '2026-11-10T00:00:00Z')
        s.notebooks[nid] = handle.runtime_id; s.handles[handle.runtime_id] = handle; s._clients[handle.runtime_id] = client; s._generations[handle.runtime_id] = 1
    observing = asyncio.create_task(sessions(app))
    await asyncio.wait_for(entered.wait(), 5)
    assert maximum == 4
    release.set()
    first = await observing
    assert len(first['items']) == 20 and first['next_cursor']
    second = await sessions(app, cursor=first['next_cursor'])
    assert len(second['items']) == 3 and second['next_cursor'] is None
    assert not {item['id'] for item in first['items']} & {item['id'] for item in second['items']}
    assert maximum == 4


@pytest.mark.anyio
async def test_execution_owner_lost_during_gateway_read_cannot_become_idle(dashboard):
    app, root, store, journal = dashboard
    nid = seed(dashboard)
    client, handle = live(app, nid)
    document = store.edit_cell(nid, 1, 'insert', text='42')
    receipt, _ = store.begin(nid, document['cells'][0]['id'], 2)
    store.update_execution(nid, receipt['execution_id'], state='running', runtime_id=handle.runtime_id, generation=1, kernel_id='kernel')
    task = asyncio.create_task(asyncio.Event().wait())
    app.state.notebook_service.tasks[nid] = task
    entered, release = asyncio.Event(), asyncio.Event()
    original = client.list_kernels
    async def inventory():
        entered.set(); await release.wait()
        return await original()
    client.list_kernels = inventory
    observing = asyncio.create_task(sessions(app))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        app.state.notebook_service.tasks.clear()
        release.set()
        assert (await observing)['items'][0]['state'] == 'Needs attention'
    finally:
        release.set(); task.cancel()
        await asyncio.gather(task, observing, return_exceptions=True)
