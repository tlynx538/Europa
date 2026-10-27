"""Bounded browser-facing API contracts against the existing ARM64 runc provider."""
import asyncio

import httpx
import pytest

from main import create_app
from test_notebook_integration import documents, executed
from test_containerd_integration import sandbox
from test_notebooks import code_notebook, nbid, rev

pytestmark = [pytest.mark.containerd_integration, pytest.mark.parametrize('sandbox', ['runc'], indirect=True)]


async def test_browser_receipt_and_interrupt_contract_on_runc(documents):
    supervisor, store, service = documents
    doc = code_notebook(store, 'print(42)'); notebook = nbid(doc)
    app = create_app(); app.state.supervisor = supervisor
    app.state.notebook_store = store; app.state.notebook_service = service
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://europa') as http:
        assert (await http.get(f'/api/notebooks/{notebook}/session')).json()['state'] == 'disconnected'
        doc, receipt = await executed(service, store, doc)
        assert receipt['result']['status'] == 'ok'
        public = (await http.get(f'/api/notebooks/{notebook}/executions/{receipt["execution_id"]}')).json()
        assert public['result']['outputs'][0]['text'] == '42\n'
        assert 'kernel_session' not in public['result'] and 'runtime_id' not in public
        session = (await http.get(f'/api/notebooks/{notebook}/session')).json()
        assert session == {'state': 'connected', 'ready': True, 'kernels': 1}
        client = await supervisor.client_for(notebook)
        kernel = receipt['kernel_id']; raw = client.client
        doc = store.edit_cell(notebook, rev(doc), 'edit', doc['cells'][0]['id'], text='import threading; threading.Event().wait(120)')
        async with raw._session_ready().ws_connect(raw._websocket_url(kernel, 'web-interrupt')) as observer:
            await raw._handshake(observer, 'web-interrupt')
            accepted = await service.submit(notebook, doc['cells'][0]['id'], rev(doc))
            async with asyncio.timeout(20):
                while (await raw._receive(observer))['header'].get('msg_type') != 'execute_input': pass
            response = await http.post(f'/api/notebooks/{notebook}/executions/{accepted["execution_id"]}/interrupt')
            assert response.status_code == 204
            await asyncio.wait_for(asyncio.gather(*tuple(service.tasks.values())), 20)
        final = store.execution(notebook, accepted['execution_id'])
        assert final['state'] == 'applied' and final['result']['status'] == 'error'
        assert final['result']['outputs'][0]['ename'] == 'KeyboardInterrupt'
