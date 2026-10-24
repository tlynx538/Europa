"""Bounded real containerd+runc standard document execution and crash recovery."""
import asyncio
import json
import os
from pathlib import Path
import sys

import httpx
import nbformat
import pytest
import pytest_asyncio

from main import create_app
from notebooks.service import NotebookService
from notebooks.store import NotebookStore
from runtime.lifecycle import IdlePolicy
from test_containerd_integration import sandbox
from test_prewarming_integration import new_control, dispose, SMALL
from test_notebooks import code_notebook, nbid, rev

pytestmark = [pytest.mark.containerd_integration, pytest.mark.parametrize('sandbox', ['runc'], indirect=True)]


def streams(cell):
    return ''.join(o['text'] for o in cell['outputs'] if o['output_type'] == 'stream')


async def executed(service, store, doc, cell=None, **kwargs):
    cell = cell or doc['cells'][-1]['id']
    receipt = await service.submit(nbid(doc), cell, rev(doc), **kwargs)
    await asyncio.wait_for(asyncio.gather(*tuple(service.tasks.values())), 40)
    saved = store.execution(nbid(doc), receipt['execution_id'])
    assert saved['state'] == 'applied', saved
    current = store.open(nbid(doc)); nbformat.validate(current)
    return current, saved


@pytest_asyncio.fixture
async def documents(sandbox, tmp_path):
    provider, _ = sandbox
    await provider.assert_empty_namespace()
    s = await new_control(provider, tmp_path / 'registry/runtime.sqlite3', target=0)
    s.enable_lifecycle(IdlePolicy(False,1800,0,30))
    store = NotebookStore(tmp_path / 'documents')
    service = NotebookService(store, s)
    try: yield s, store, service
    finally:
        # On assertion failure, explicitly interrupt the disposable probe before owned cleanup.
        for client in list(s._clients.values()):
            for kernel in list(client._busy): await client.interrupt_kernel(kernel)
        await service.close(grace=5)
        await dispose(s); store.close()
        await provider.assert_empty_namespace()


async def test_real_document_survives_runtime_deletion_and_clean_kernel(documents, tmp_path):
    s, store, service = documents
    doc = code_notebook(store, 'saved_value=42; print(saved_value)')
    notebook = nbid(doc)
    doc, receipt = await executed(service, store, doc)
    assert receipt['result']['status'] == 'ok' and streams(doc['cells'][0]) == '42\n'
    runtime = s.notebooks[notebook]
    await s.destroy_notebook(notebook)
    assert store.open(notebook) == doc
    assert s.registry.get(runtime)['phase'] == 'destroyed'
    store.close()
    reopened = NotebookStore(tmp_path / 'documents'); service.store = reopened
    try:
        doc = reopened.open(notebook)
        assert streams(doc['cells'][0]) == '42\n'
        doc = reopened.edit_cell(notebook, rev(doc), 'insert', text="print('saved_value' in globals())")
        doc, receipt = await executed(service, reopened, doc)
        assert receipt['result']['status'] == 'ok' and streams(doc['cells'][1]) == 'False\n'
        assert s.notebooks[notebook] != runtime
        client = await s.client_for(notebook)
        await client.restart_kernel(receipt['kernel_id'])
        doc, receipt = await executed(service, reopened, doc)
        assert streams(doc['cells'][1]) == 'False\n' and receipt['result']['execution_count'] == 1
        nbformat.validate(nbformat.read(tmp_path/'documents'/(notebook+'.ipynb'), as_version=4))
    finally:
        await service.close(); reopened.close()


async def test_real_rich_outputs_updates_errors_and_clear(documents):
    s, store, service = documents
    doc = code_notebook(store, "import sys\nfrom IPython.display import display, HTML, clear_output\nprint('stdout')\nprint('stderr', file=sys.stderr)\ndh=display(HTML('<b>old</b>'), display_id=True)")
    doc, receipt = await executed(service, store, doc)
    assert receipt['result']['status'] == 'ok'
    assert {o['name'] for o in doc['cells'][0]['outputs'] if o['output_type']=='stream'} == {'stdout','stderr'}
    doc = store.edit_cell(nbid(doc),rev(doc),'insert',text="dh.update(HTML('<i>updated</i>'))\n6*7")
    doc, receipt = await executed(service,store,doc)
    assert any(o.get('data',{}).get('text/html') == '<i>updated</i>' for o in doc['cells'][0]['outputs'])
    assert doc['cells'][1]['outputs'][0]['data']['text/plain'] == '42'
    doc = store.edit_cell(nbid(doc),rev(doc),'insert',text="print('discard'); clear_output(wait=True); print('kept')")
    doc, receipt = await executed(service,store,doc)
    assert streams(doc['cells'][2]) == 'kept\n'
    doc = store.edit_cell(nbid(doc),rev(doc),'insert',text="raise ValueError('expected error')")
    doc, receipt = await executed(service,store,doc)
    assert receipt['result']['status'] == 'error'
    output = doc['cells'][3]['outputs'][0]
    assert output['output_type'] == 'error' and output['ename'] == 'ValueError' and output['traceback']


async def test_real_http_disconnect_and_interrupted_cell(documents):
    s, store, service = documents
    doc = code_notebook(store,'import threading; threading.Event().wait(120)')
    notebook, cell = nbid(doc), doc['cells'][0]['id']
    client = await s.client_for(notebook); kernel = (await client.create_kernel())['id']
    raw = client.client
    app = create_app(); app.state.supervisor=s; app.state.notebook_store=store; app.state.notebook_service=service
    async with raw._session_ready().ws_connect(raw._websocket_url(kernel,'interrupt-observer')) as observer:
        await raw._handshake(observer,'interrupt-observer')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://europa') as http:
            response = await http.post(f'/api/notebooks/{notebook}/cells/{cell}/execute',json={'revision':rev(doc),'kernel_id':kernel,'timeout':150})
            assert response.status_code == 202
            receipt = response.json()
        async with asyncio.timeout(20):
            while (await raw._receive(observer))['header'].get('msg_type') != 'execute_input': pass
        assert (await raw.inspect_kernel(kernel))['execution_state'] == 'busy'
        await client.interrupt_kernel(kernel)
        await asyncio.gather(*tuple(service.tasks.values()))
    assert store.execution(notebook,receipt['execution_id'])['state'] == 'applied'
    current=store.open(notebook)
    assert current['cells'][0]['outputs'][0]['ename'] == 'KeyboardInterrupt'
    assert current['cells'][0]['metadata']['europa']['status'] == 'error'


@pytest.mark.parametrize('boundary', ['applied','completed','running'])
async def test_real_notebook_crash_recovery(sandbox, tmp_path, monkeypatch, boundary):
    provider, _ = sandbox
    await provider.assert_empty_namespace()
    path = tmp_path/'registry/runtime.sqlite3'
    monkeypatch.setenv('EUROPA_RUNTIME_REGISTRY',str(path))
    monkeypatch.setenv('EUROPA_WARM_TARGET','0')
    monkeypatch.setenv('EUROPA_RESOURCE_PROFILES',json.dumps({'standard':vars(SMALL)}))
    monkeypatch.setenv('EUROPA_ADMISSION_CPU_CAPACITY_CORES','.5')
    monkeypatch.setenv('EUROPA_ADMISSION_MEMORY_CAPACITY_BYTES',str(768*1024**2))
    checkpoint=tmp_path/'checkpoint.json'
    with (tmp_path/'child.log').open('wb') as log:
        child=await asyncio.create_subprocess_exec(sys.executable,'tests/notebook_process.py',str(checkpoint),boundary,
            stdout=log,stderr=log,env=dict(os.environ,PYTHONPATH=str(Path('api/src').resolve())))
        try:
            async with asyncio.timeout(50):
                while not checkpoint.exists():
                    if child.returncode is not None: raise AssertionError('Checkpoint failed; inspect private child log')
                    await asyncio.sleep(.05)
            before=json.loads(checkpoint.read_text())
        finally:
            if child.returncode is None: child.kill()
            await child.wait()
    assert child.returncode == -9
    s=await new_control(provider,path,target=0)
    s.enable_lifecycle(IdlePolicy(False,1800,0,30))
    store=NotebookStore(os.environ['EUROPA_NOTEBOOK_ROOT']); service=NotebookService(store,s)
    try:
        notebook=before['notebook_id']; doc=store.open(notebook)
        receipt=store.execution(notebook,before['execution_id'])
        if boundary=='running':
            assert receipt['state']=='uncertain'
            assert doc['cells'][0]['metadata']['europa']['status']=='uncertain'
            assert s.registry.get(before['runtime_id'])['phase']=='reconciliation'
            assert await s.admission.reservation(notebook) is not None
        else:
            assert receipt['state']=='applied' and streams(doc['cells'][0])=='42\n'
            assert s.notebooks[notebook]==before['runtime_id']
            doc=store.edit_cell(notebook,rev(doc),'edit',before['cell_id'],text='print(saved_value)')
            doc, receipt=await executed(service,store,doc)
            assert streams(doc['cells'][0])=='42\n' and receipt['kernel_id']==before['kernel_id']
    finally:
        await service.close(); await dispose(s); store.close()
        await provider.assert_empty_namespace()


async def test_real_idle_cleanup_preserves_saved_document(documents):
    from test_lifecycle import Clock
    s, store, service=documents
    clock=Clock(); s.lifecycle.clock=clock
    s.lifecycle.policy=IdlePolicy(True,60,0,30)
    doc=code_notebook(store)
    doc,receipt=await executed(service,store,doc)
    identity=s.notebooks[nbid(doc)]
    clock.advance(61); await s.lifecycle.sweep()
    assert s.registry.get(identity)['phase']=='destroyed'
    assert store.open(nbid(doc))==doc and streams(doc['cells'][0])=='42\n'
    assert (await s.admission.metrics())['reservation_count']==0


async def test_real_notebook_cannot_execute_in_another_notebooks_kernel(documents):
    s, store, service=documents
    first=code_notebook(store); first,receipt=await executed(service,store,first)
    second=code_notebook(store,'print("wrong notebook")')
    pending=await service.submit(nbid(second),second['cells'][0]['id'],rev(second),kernel_id=receipt['kernel_id'])
    await asyncio.gather(*tuple(service.tasks.values()))
    rejected=store.execution(nbid(second),pending['execution_id'])
    assert rejected['state']=='uncertain' and rejected['failure']=='GatewayError'
    assert not store.open(nbid(second))['cells'][0]['outputs']
    assert s.notebooks[nbid(first)]!=s.notebooks[nbid(second)]
