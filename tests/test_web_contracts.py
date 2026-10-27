"""Sanitized browser contracts, existing-kernel interrupt and localhost boundary."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from main import create_app
from notebooks.service import NotebookService
from notebooks.store import NotebookError, NotebookStore
from test_notebooks import code_notebook, nbid, rev, result


@pytest.fixture
def browser_app(tmp_path):
    store = NotebookStore(tmp_path / 'documents')
    app = create_app()
    app.state.notebook_store = store
    app.state.supervisor = SimpleNamespace(notebooks={})
    yield app, store
    store.close()


async def test_browser_receipt_projection_and_session_do_not_expose_operational_state(browser_app):
    app, store = browser_app
    doc = code_notebook(store); notebook = nbid(doc)
    receipt, _ = store.begin(notebook, doc['cells'][0]['id'], rev(doc))
    store.update_execution(notebook, receipt['execution_id'], runtime_id='private-runtime', generation=8,
                           kernel_id='private-kernel')
    store.finish(notebook, receipt['execution_id'], result())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://europa') as client:
        response = await client.get(f'/api/notebooks/{notebook}/executions/{receipt["execution_id"]}')
        assert response.status_code == 200
        assert set(response.json()) == {'notebook_id', 'cell_id', 'execution_id', 'state', 'created_at', 'source_hash', 'result'}
        assert set(response.json()['result']) == {'status', 'execution_count', 'outputs'}
        assert 'private-' not in response.text and 'kernel_session' not in response.text
        assert (await client.get(f'/api/notebooks/{notebook}/session')).json() == {
            'state': 'disconnected', 'ready': False, 'kernels': 0}
        assert app.state.supervisor.notebooks == {}


async def test_loopback_headers_and_cross_site_browser_mutations(browser_app):
    app, _ = browser_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8000') as client:
        response = await client.get('/')
        assert "script-src 'self'" in response.headers['content-security-policy']
        assert "object-src 'none'" in response.headers['content-security-policy']
        assert response.headers['x-content-type-options'] == 'nosniff'
        for headers in [{'Origin': 'https://attacker.invalid'}, {'Sec-Fetch-Site': 'cross-site'}, {'Host': 'attacker.invalid'}]:
            assert (await client.post('/api/notebooks', json={'title': 'Blocked'}, headers=headers)).status_code == 403
        assert (await client.post('/api/notebooks', json={'title': 'Allowed'}, headers={'Origin': 'http://127.0.0.1:8000'})).status_code == 201
        assert (await client.get('/api/notebooks')).headers['cache-control'] == 'no-store'


async def test_interrupt_requires_current_verified_binding(browser_app):
    app, store = browser_app
    doc = code_notebook(store); notebook = nbid(doc)
    receipt, _ = store.begin(notebook, doc['cells'][0]['id'], rev(doc))
    client = SimpleNamespace(inspect_kernel=AsyncMock(), interrupt_kernel=AsyncMock())
    supervisor = SimpleNamespace(notebooks={notebook: 'runtime'}, handles={'runtime': SimpleNamespace(generation=2)},
                                 _clients={'runtime': client}, _lock=lambda _: asyncio.Lock(),
                                 inspect=AsyncMock(return_value=SimpleNamespace(ready=True, handle=SimpleNamespace(generation=2))))
    service = NotebookService(store, supervisor)
    app.state.notebook_service = service
    for generation in (1, 2):
        store.update_execution(notebook, receipt['execution_id'], state='running', runtime_id='runtime', generation=generation, kernel_id='kernel')
        if generation == 1:
            with pytest.raises(NotebookError): await service.interrupt(notebook, receipt['execution_id'])
            client.interrupt_kernel.assert_not_awaited()
        else:
            await service.interrupt(notebook, receipt['execution_id'])
            client.interrupt_kernel.assert_awaited_once_with('kernel')
    store.uncertain(notebook, receipt['execution_id'])
    with pytest.raises(NotebookError): await service.interrupt(notebook, receipt['execution_id'])
    assert client.interrupt_kernel.await_count == 1


async def test_disconnected_interrupt_waiter_keeps_new_execution_admission_blocked(browser_app):
    _, store = browser_app
    doc = code_notebook(store); notebook = nbid(doc)
    receipt, _ = store.begin(notebook, doc['cells'][0]['id'], rev(doc))
    store.update_execution(notebook, receipt['execution_id'], state='running', runtime_id='runtime', generation=2, kernel_id='kernel')
    entered, release = asyncio.Event(), asyncio.Event()
    async def interrupt(_): entered.set(); await release.wait()
    client = SimpleNamespace(inspect_kernel=AsyncMock(), interrupt_kernel=interrupt)
    supervisor = SimpleNamespace(notebooks={notebook:'runtime'}, handles={'runtime':SimpleNamespace(generation=2)},
        _clients={'runtime':client}, _lock=lambda _: asyncio.Lock(), inspect=AsyncMock(return_value=SimpleNamespace(ready=True, handle=SimpleNamespace(generation=2))))
    service = NotebookService(store, supervisor)
    caller = asyncio.create_task(service.interrupt(notebook, receipt['execution_id']))
    await entered.wait(); caller.cancel()
    with pytest.raises(asyncio.CancelledError): await caller
    with pytest.raises(NotebookError, match='interrupt'):
        await service.submit(notebook, doc['cells'][0]['id'], rev(store.open(notebook)))
    assert notebook in service.interrupts
    pending = tuple(service.interrupts.values()); release.set(); await asyncio.gather(*pending)
    assert notebook not in service.interrupts
    await service.close()
