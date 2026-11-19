"""Top-level GET /health: side-effect free API + Gateway state, no secrets, loopback boundary."""
import asyncio

import httpx
import pytest
import pytest_asyncio

import internal.health as health
from kernel.gateway_client import GatewayError
from runtime.contracts import GatewayEndpoint, RuntimeHandle, RuntimeState
from runtime.supervisor import RuntimeSupervisor
from agent_support import Harness, reply

SECRET_URL, SECRET_TOKEN, SECRET_RUNTIME = 'http://127.0.0.1:18888', 'secret-gateway-token', 'runtime-secret-identity'


class RecordingProvider:
    """Fails loudly if anything tries to provision, inspect or destroy a runtime."""
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        async def record(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError(f'/health must not call provider.{name}')
        return record


class Client:
    def __init__(self, error=None, delay=0.0):
        self.error, self.delay, self.calls, self._closing = error, delay, 0, False

    async def health(self):
        self.calls += 1
        if self.delay: await asyncio.sleep(self.delay)
        if self.error: raise self.error
        return []


@pytest_asyncio.fixture
async def made(tmp_path):
    harness = Harness(tmp_path, [reply('unused')])
    provider = RecordingProvider()
    supervisor = RuntimeSupervisor({'local': provider})
    harness.app.state.supervisor = supervisor
    yield harness, supervisor, provider
    await harness.stop()
    try: harness.close()
    except Exception: pass


def bind(supervisor, client=None, state=RuntimeState.READY, provider='local'):
    handle = RuntimeHandle(SECRET_RUNTIME, provider, 'default', GatewayEndpoint(SECRET_URL, SECRET_TOKEN), 1, state, 'now')
    supervisor.handles[handle.runtime_id] = handle
    supervisor.notebooks['default'] = handle.runtime_id
    supervisor._generations[handle.runtime_id] = 1
    if client is not None: supervisor._clients[handle.runtime_id] = client
    return handle


async def get(app, path='/health', method='get', host='europa'):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f'http://{host}') as client:
        return await getattr(client, method)(path)


def assert_private(response, tmp_path):
    for secret in (SECRET_URL, '18888', SECRET_TOKEN, SECRET_RUNTIME, str(tmp_path), 'kernel'):
        assert secret not in response.text


async def test_fresh_app_reports_gateway_stopped_without_starting_anything(made, tmp_path):
    h, supervisor, provider = made
    response = await get(h.app)
    assert response.status_code == 200
    assert response.json() == {'status': 'ok', 'api': 'ok', 'gateway': 'stopped', 'agents': 'disabled'}
    assert response.headers['cache-control'] == 'no-store'
    head = await get(h.app, method='head')
    assert head.status_code == 200 and head.headers['cache-control'] == 'no-store'
    assert provider.calls == []
    assert supervisor.handles == {} and supervisor._clients == {} and supervisor.notebooks == {} and supervisor._locks == {}
    assert_private(response, tmp_path)


async def test_bound_gateway_is_probed_once_and_reported_running(made, tmp_path):
    h, supervisor, provider = made
    client = Client()
    bind(supervisor, client)
    response = await get(h.app)
    assert response.status_code == 200 and response.json()['gateway'] == 'running'
    assert client.calls == 1 and provider.calls == [] and list(supervisor.handles) == [SECRET_RUNTIME]
    assert_private(response, tmp_path)


@pytest.mark.parametrize('client,expected', [
    (Client(error=GatewayError(f'Gateway at {SECRET_URL} rejected {SECRET_TOKEN}', 502)), 'unreachable'),
    (Client(delay=5), 'unreachable'),
    (None, 'starting'),
])
async def test_unreachable_slow_and_starting_gateways_still_return_200(made, tmp_path, monkeypatch, client, expected):
    monkeypatch.setattr(health, 'PROBE_SECONDS', 0.05)
    h, supervisor, provider = made
    bind(supervisor, client)
    started = asyncio.get_running_loop().time()
    response = await get(h.app)
    assert asyncio.get_running_loop().time() - started < 2
    assert response.status_code == 200 and response.json()['status'] == 'ok' and response.json()['gateway'] == expected
    assert provider.calls == []
    assert_private(response, tmp_path)


async def test_stopping_failed_and_provisioning_states(made):
    h, supervisor, _ = made
    handle = bind(supervisor, Client())
    supervisor._unavailable.add(handle.runtime_id)
    assert (await get(h.app)).json()['gateway'] == 'stopping'
    supervisor._unavailable.clear()
    supervisor.handles[handle.runtime_id] = RuntimeHandle(handle.runtime_id, 'local', 'default', None, 1, RuntimeState.FAILED, 'now')
    assert (await get(h.app)).json()['gateway'] == 'failed'
    supervisor.handles.clear(); supervisor._clients.clear(); supervisor.notebooks.clear()
    lock = supervisor._lock('default')
    async with lock:  # provisioning has the notebook lock but has not published a handle yet
        assert (await get(h.app)).json()['gateway'] == 'starting'
    assert (await get(h.app)).json()['gateway'] == 'stopped'


async def test_closed_application_database_is_503_naming_the_component(made, tmp_path):
    h, supervisor, _ = made
    h.application.close()
    response = await get(h.app)
    assert response.status_code == 503 and response.headers['cache-control'] == 'no-store'
    assert response.json() == {'status': 'unavailable', 'api': 'unavailable', 'gateway': 'stopped', 'agents': 'disabled',
                               'unavailable': ['application_storage']}
    assert_private(response, tmp_path)


async def test_unreadable_database_and_missing_notebook_storage_are_503(made, tmp_path, monkeypatch):
    h, _, _ = made

    class Broken:
        def execute(self, *_):
            raise __import__('sqlite3').DatabaseError(f'file is not a database: {tmp_path}/application.sqlite3')
        def close(self): pass
    real = h.application.db
    h.application.db = Broken()
    response = await get(h.app)
    assert response.status_code == 503 and response.json()['unavailable'] == ['application_storage']
    assert_private(response, tmp_path)
    h.application.db = real
    h.notebooks.close()
    response = await get(h.app)
    assert response.status_code == 503 and response.json()['unavailable'] == ['notebook_storage']
    del h.app.state.application_store
    response = await get(h.app)
    assert response.json()['unavailable'] == ['application_storage', 'notebook_storage']


async def test_agents_running_is_reported(made):
    h, _, _ = made
    await h.start()
    assert (await get(h.app)).json()['agents'] == 'running'


async def test_non_loopback_host_is_rejected(made):
    h, _, provider = made
    response = await get(h.app, host='health.example.com')
    assert response.status_code == 403 and 'gateway' not in response.text and provider.calls == []


async def test_api_health_and_spa_root_are_unchanged(made):
    h, supervisor, _ = made
    requested = []

    async def client_for(notebook_id='default', **_):
        requested.append(notebook_id)
        raise GatewayError('Gateway request failed', 502)
    supervisor.client_for = client_for
    assert (await get(h.app, '/api/health')).status_code == 502 and requested == ['default']
    root = await get(h.app, '/')
    assert root.status_code == 200 and '"gateway"' not in root.text  # SPA index or API banner, not the health body
    assert (await get(h.app)).json()['gateway'] == 'stopped' and requested == ['default']
