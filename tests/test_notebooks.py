"""Standard files, atomicity, revision races, owned execution and protocol reduction."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from types import SimpleNamespace

import nbformat
import pytest

from kernel.gateway_client import GatewayError
from notebooks.outputs import notebook_result
from notebooks.service import NotebookService
from notebooks.store import NotebookError, NotebookStore
from test_gateway_protocol import message


@pytest.fixture
def store(tmp_path):
    value = NotebookStore(tmp_path / 'documents')
    yield value
    value.close()


def meta(doc): return doc['metadata']['europa']
def nbid(doc): return meta(doc)['notebook_id']
def rev(doc): return meta(doc)['revision']


def code_notebook(store, text='print(42)'):
    doc = store.create('Example')
    return store.edit_cell(nbid(doc), rev(doc), 'insert', text=text)


def result(*outputs, status='ok', count=1):
    return {'status': status, 'execution_count': count, 'outputs': list(outputs), 'display_map': {}, 'display_updates': {}, 'protocol_execution_id':'protocol-id','kernel_session':'kernel-session'}


def test_crud_standard_nbformat_and_titles_are_not_paths(store, tmp_path):
    doc = code_notebook(store)
    identity = nbid(doc)
    stored = nbformat.read(tmp_path / 'documents' / (identity + '.ipynb'), as_version=4)
    nbformat.validate(stored)
    assert stored.cells[0].source == 'print(42)'
    assert store.list()[0]['notebook_id'] == identity
    doc = store.rename(identity, rev(doc), '../../a title')
    assert meta(store.open(identity))['title'] == '../../a title'
    doc['metadata']['custom'] = {'preserved':True}; doc['cells'][0]['metadata']['tags'] = ['important']
    doc = store.save(identity, rev(doc), doc)
    assert store.open(identity) == doc
    store.delete(identity, rev(doc)); assert not store.list()
    with pytest.raises(NotebookError) as exc: store.open(identity)
    assert exc.value.status_code == 404


def test_cell_order_type_metadata_and_clear(store):
    doc = code_notebook(store); identity = nbid(doc); first = doc['cells'][0]['id']
    doc = store.edit_cell(identity, rev(doc), 'insert', cell_type='markdown', text='# Notes', index=0, metadata={'tags':['notes']})
    second = doc['cells'][0]['id']
    doc = store.edit_cell(identity, rev(doc), 'move', first, index=0)
    assert [c['id'] for c in doc['cells']] == [first, second]
    doc = store.edit_cell(identity, rev(doc), 'type', second, cell_type='code')
    assert doc['cells'][1]['id'] == second and doc['cells'][1]['outputs'] == []
    doc = store.edit_cell(identity, rev(doc), 'edit', second, text='updated', metadata={'custom':42})
    assert doc['cells'][1]['metadata'] == {'custom':42}
    doc = store.edit_cell(identity, rev(doc), 'clear', second)
    doc = store.edit_cell(identity, rev(doc), 'delete', first)
    assert [c['id'] for c in doc['cells']] == [second]
    nbformat.validate(doc)


@pytest.mark.parametrize('identity', ['../outside', '/etc/passwd', 'x'*32, '', 'a'*31])
def test_path_traversal_rejected(store, identity):
    with pytest.raises(NotebookError): store.open(identity)


def test_symlinks_root_document_and_hardlinks_rejected(tmp_path):
    actual = tmp_path / 'actual'; actual.mkdir(mode=0o700)
    link = tmp_path / 'link'; link.symlink_to(actual, target_is_directory=True)
    with pytest.raises(NotebookError): NotebookStore(link)
    store = NotebookStore(actual)
    try:
        outside = tmp_path / 'outside'; outside.write_text('private'); outside.chmod(0o600)
        path = actual / ('a'*32 + '.ipynb'); path.symlink_to(outside)
        with pytest.raises(NotebookError): store.open('a'*32)
        path.unlink(); os.link(outside, path)
        with pytest.raises(NotebookError): store.open('a'*32)
        assert outside.read_text() == 'private'
    finally: store.close()


def test_exclusive_storage_owner(store, tmp_path):
    with pytest.raises(NotebookError, match='owner'):
        NotebookStore(tmp_path / 'documents')


def test_atomic_replace_failure_preserves_previous_document(store, monkeypatch):
    doc = code_notebook(store); original = store.open(nbid(doc))
    def fail(*args, **kwargs): raise OSError('injected interrupted write')
    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(OSError): store.rename(nbid(doc), rev(doc), 'unsaved')
    assert store.open(nbid(doc)) == original
    assert not any(n.startswith('.write-') for n in os.listdir(store.fd))


def test_concurrent_revision_conflict_commits_exactly_once(store):
    doc = code_notebook(store)
    def write(title):
        try: return store.rename(nbid(doc), rev(doc), title)
        except NotebookError as exc: return exc.status_code
    with ThreadPoolExecutor(2) as pool: results = list(pool.map(write, ['A','B']))
    assert sum(isinstance(r, dict) for r in results) == 1 and 409 in results
    assert rev(store.open(nbid(doc))) == rev(doc)+1


@pytest.mark.parametrize('mutation', ['duplicate','missing','invalid_format','raw','forged_identity'])
def test_invalid_full_documents_rejected_without_writing(store, mutation):
    doc = code_notebook(store); original = copy.deepcopy(doc)
    if mutation == 'duplicate': doc['cells'].append(copy.deepcopy(doc['cells'][0]))
    elif mutation == 'missing': del doc['cells'][0]['id']
    elif mutation == 'invalid_format': doc['nbformat'] = 3
    elif mutation == 'raw': doc['cells'][0]['cell_type'] = 'raw'
    else: meta(doc)['notebook_id'] = 'a'*32
    with pytest.raises(NotebookError): store.save(nbid(original), rev(original), doc)
    assert store.open(nbid(original)) == original


@pytest.mark.parametrize('change', ['edit','move','delete','newer_execution','delete_notebook'])
def test_late_results_never_overwrite_new_revisions(store, change):
    doc = code_notebook(store); notebook, cell = nbid(doc), doc['cells'][0]['id']
    receipt, _ = store.begin(notebook, cell, rev(doc))
    doc = store.open(notebook)
    if change == 'delete_notebook': store.delete(notebook, rev(doc))
    elif change == 'newer_execution': store.begin(notebook, cell, rev(doc))
    else: store.edit_cell(notebook, rev(doc), change, cell, text='different', index=0)
    saved = store.finish(notebook, receipt['execution_id'], result(nbformat.v4.new_output('stream',name='stdout',text='42\n')))
    assert saved['state'] == 'conflict' and saved['result']['outputs'][0]['text'] == '42\n'
    if change != 'delete_notebook':
        current = store.open(notebook)
        assert not any(c.get('outputs') for c in current['cells'])


def test_completed_receipt_replays_after_interrupted_cell_commit(store, tmp_path, monkeypatch):
    doc = code_notebook(store); notebook, cell = nbid(doc), doc['cells'][0]['id']
    receipt, _ = store.begin(notebook, cell, rev(doc))
    original = store._commit
    def fail(document): raise OSError('interruption')
    monkeypatch.setattr(store, '_commit', fail)
    with pytest.raises(OSError): store.finish(notebook, receipt['execution_id'], result())
    assert store.execution(notebook, receipt['execution_id'])['state'] == 'completed'
    monkeypatch.setattr(store, '_commit', original)
    store.close()
    reopened = NotebookStore(tmp_path / 'documents')
    try:
        assert reopened.execution(notebook, receipt['execution_id'])['state'] == 'applied'
        assert reopened.open(notebook)['cells'][0]['execution_count'] == 1
    finally: reopened.close()


def test_startup_never_infers_execution_completion(store, tmp_path):
    doc = code_notebook(store); notebook, cell = nbid(doc), doc['cells'][0]['id']
    receipt, _ = store.begin(notebook, cell, rev(doc)); store.close()
    reopened = NotebookStore(tmp_path / 'documents')
    try:
        assert reopened.execution(notebook, receipt['execution_id'])['state'] == 'uncertain'
        assert reopened.open(notebook)['cells'][0]['metadata']['europa']['status'] == 'uncertain'
    finally: reopened.close()


def test_output_reduction_mime_clear_update_and_counts():
    def msg(kind, content): return message('protocol', 'iopub', kind, content)
    messages = [msg('stream', {'name':'stderr','text':'kept until clear'}), msg('clear_output', {'wait':True}),
        msg('display_data', {'data':{'text/html':'<b>old</b>', 'application/json':{'a':1}}, 'metadata':{'custom':True}, 'transient':{'display_id':'display'}}),
        msg('update_display_data', {'data':{'text/html':'<b>new</b>'}, 'transient':{'display_id':'display'}}),
        msg('stream', {'name':'stdout','text':'42\n'}),
        msg('execute_result', {'data':{'text/plain':'42'},'execution_count':7}),
        msg('error', {'ename':'ValueError','evalue':'example','traceback':['frame']}),
        message('foreign','iopub','stream',{'name':'stdout','text':'foreign'})]
    converted = notebook_result({'execution_id':'protocol','reply':{'status':'error','execution_count':7},'messages':messages})
    assert [o['output_type'] for o in converted['outputs']] == ['display_data','stream','execute_result','error']
    assert converted['outputs'][0]['data'] == {'text/html':'<b>new</b>'}
    assert converted['execution_count'] == 7
    doc = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(outputs=converted['outputs'],execution_count=7)])
    nbformat.validate(doc)
    messages.append(msg('clear_output', {'wait':False}))
    assert notebook_result({'execution_id':'protocol','reply':{'status':'error'},'messages':messages})['outputs'] == []


class Client:
    def __init__(self):
        self.started, self.release = asyncio.Event(), asyncio.Event()
        self.kernels = set(); self.calls=0
    async def create_kernel(self): self.kernels.add('kernel'); return {'id':'kernel'}
    async def inspect_kernel(self, kernel):
        if kernel not in self.kernels: raise GatewayError('Unknown kernel',404)
        return {'id':kernel}
    async def execute(self, kernel, code, timeout=30):
        self.calls += 1; self.started.set(); await self.release.wait()
        return {'execution_id':'protocol','reply':{'status':'ok','execution_count':1},
            'messages':[message('protocol','iopub','stream',{'name':'stdout','text':'42\n'})]}


def service(store):
    client = Client()
    supervisor = SimpleNamespace(notebooks={}, handles={})
    async def client_for(notebook):
        supervisor.notebooks[notebook] = 'runtime'
        supervisor.handles['runtime'] = SimpleNamespace(generation=1)
        return client
    supervisor.client_for = client_for
    return NotebookService(store, supervisor), client


async def test_owned_execution_and_edits_without_long_storage_lock(store):
    doc = code_notebook(store); notebook, cell = nbid(doc), doc['cells'][0]['id']
    execution, client = service(store)
    receipt = await execution.submit(notebook, cell, rev(doc))
    await client.started.wait()
    with pytest.raises(NotebookError): await execution.submit(notebook, cell, rev(store.open(notebook)))
    current = store.open(notebook)
    store.edit_cell(notebook, rev(current), 'edit', cell, text='new source')
    tasks = tuple(execution.tasks.values()); client.release.set(); await asyncio.gather(*tasks)
    assert store.execution(notebook, receipt['execution_id'])['state'] == 'conflict'
    assert store.open(notebook)['cells'][0]['source'] == 'new source'
    await execution.close()


async def test_request_cancellation_does_not_cancel_document_execution(store, monkeypatch):
    doc = code_notebook(store); notebook, cell = nbid(doc), doc['cells'][0]['id']
    execution, client = service(store)
    # Cancel the caller during the initial durable commit, before the 202 response.
    import threading
    entered, release = threading.Event(), threading.Event()
    original = store.begin
    def blocked(*args): entered.set(); release.wait(5); return original(*args)
    monkeypatch.setattr(store, 'begin', blocked)
    caller = asyncio.create_task(execution.submit(notebook, cell, rev(doc)))
    await asyncio.to_thread(entered.wait, 5)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError): await caller
    release.set(); await client.started.wait()
    tasks = tuple(execution.tasks.values()); client.release.set(); await asyncio.gather(*tasks)
    assert store.open(notebook)['cells'][0]['outputs'][0]['text'] == '42\n'
    await execution.close()


async def test_stale_execution_request_has_no_runtime_effect(store):
    doc = code_notebook(store); execution, client = service(store)
    with pytest.raises(NotebookError): await execution.submit(nbid(doc), doc['cells'][0]['id'], 1)
    assert client.calls == 0
    await execution.close()


def test_display_updates_from_later_cells_are_scoped_to_kernel_identity(store):
    doc = code_notebook(store); notebook = nbid(doc); first = doc['cells'][0]['id']
    receipt, _ = store.begin(notebook, first, rev(doc))
    store.update_execution(notebook, receipt['execution_id'], runtime_id='runtime',generation=1,kernel_id='kernel')
    display = result(nbformat.v4.new_output('display_data', data={'text/plain':'old'}))
    display['display_map'] = {'display':[0]}
    store.finish(notebook, receipt['execution_id'], display)
    doc = store.open(notebook)
    doc = store.edit_cell(notebook, rev(doc), 'insert', text='update_display()')
    second = doc['cells'][1]['id']
    receipt, _ = store.begin(notebook, second, rev(doc))
    store.update_execution(notebook, receipt['execution_id'], runtime_id='runtime',generation=1,kernel_id='kernel')
    update = result(); update['display_updates'] = {'display':{'data':{'text/plain':'new'},'metadata':{}}}
    store.finish(notebook, receipt['execution_id'], update)
    assert store.open(notebook)['cells'][0]['outputs'][0]['data']['text/plain'] == 'new'
    doc = store.open(notebook)
    receipt, _ = store.begin(notebook, second, rev(doc))
    store.update_execution(notebook, receipt['execution_id'], runtime_id='other',generation=1,kernel_id='kernel')
    update['display_updates']['display']['data']['text/plain'] = 'wrong kernel'
    store.finish(notebook, receipt['execution_id'], update)
    assert store.open(notebook)['cells'][0]['outputs'][0]['data']['text/plain'] == 'new'


def test_crash_after_document_commit_replays_idempotently(store, monkeypatch, tmp_path):
    doc = code_notebook(store); notebook, cell = nbid(doc), doc['cells'][0]['id']
    receipt, _ = store.begin(notebook, cell, rev(doc))
    original = store.update_execution
    def fail_applied(*args, **kwargs):
        if kwargs.get('state') == 'applied': raise OSError('crash before receipt acknowledgment')
        return original(*args, **kwargs)
    monkeypatch.setattr(store, 'update_execution', fail_applied)
    with pytest.raises(OSError): store.finish(notebook, receipt['execution_id'], result())
    committed = store.open(notebook)
    store.close()
    reopened = NotebookStore(tmp_path / 'documents')
    try:
        assert reopened.open(notebook) == committed
        assert reopened.execution(notebook, receipt['execution_id'])['state'] == 'applied'
    finally: reopened.close()


def test_orphan_temporary_write_is_not_a_notebook(tmp_path):
    root = tmp_path / 'documents'; root.mkdir(mode=0o700)
    temporary = root / ('.write-' + 'a'*32 + '.tmp'); temporary.write_text('{partial'); temporary.chmod(0o600)
    store = NotebookStore(root)
    try: assert store.list() == [] and not temporary.exists()
    finally: store.close()


async def test_http_notebook_and_cell_contracts(store):
    import httpx
    from main import create_app
    app = create_app(); app.state.notebook_store = store
    execution, client = service(store); app.state.notebook_service = execution
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://europa') as http:
        response = await http.post('/api/notebooks',json={'title':'Saved'})
        assert response.status_code == 201
        doc = response.json(); notebook = nbid(doc)
        doc = (await http.post(f'/api/notebooks/{notebook}/cells',json={'revision':rev(doc),'source':'print(42)'})).json()
        cell = doc['cells'][0]['id']
        assert (await http.patch(f'/api/notebooks/{notebook}',json={'revision':1,'title':'stale'})).status_code == 409
        assert (await http.put(f'/api/notebooks/{notebook}',json={'revision':rev(doc),'document':doc})).status_code == 200
        doc = (await http.get(f'/api/notebooks/{notebook}')).json()
        response = await http.post(f'/api/notebooks/{notebook}/cells/{cell}/execute',json={'revision':rev(doc)})
        assert response.status_code == 202
        receipt = response.json(); await client.started.wait()
        tasks = tuple(execution.tasks.values()); client.release.set(); await asyncio.gather(*tasks)
        saved = (await http.get(f'/api/notebooks/{notebook}/executions/{receipt["execution_id"]}')).json()
        assert saved['state'] == 'applied'
        current = (await http.get(f'/api/notebooks/{notebook}/cells/{cell}')).json()
        assert current['cell']['outputs'][0]['text'] == '42\n'
        assert (await http.delete(f'/api/notebooks/{notebook}',params={'revision':current['revision']})).status_code == 204
    await execution.close()


async def test_shutdown_keeps_document_outcome_uncertain(store):
    doc = code_notebook(store); execution, client = service(store)
    receipt = await execution.submit(nbid(doc), doc['cells'][0]['id'], rev(doc))
    await client.started.wait()
    await execution.close(grace=0)
    assert store.execution(nbid(doc), receipt['execution_id'])['state'] == 'uncertain'
    assert store.open(nbid(doc))['cells'][0]['metadata']['europa']['status'] == 'uncertain'


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), '\ud800'])
def test_nonportable_json_cannot_replace_saved_document(store, bad):
    doc = code_notebook(store); original = copy.deepcopy(doc)
    doc['metadata']['invalid'] = bad
    with pytest.raises(NotebookError): store.save(nbid(doc), rev(doc), doc)
    assert store.open(nbid(original)) == original


def test_malformed_reserved_cell_metadata_rejected(store):
    doc = code_notebook(store); original=copy.deepcopy(doc)
    doc['cells'][0]['metadata']['europa'] = 'invalid'
    with pytest.raises(NotebookError): store.save(nbid(doc), rev(doc), doc)
    assert store.open(nbid(original)) == original


def test_deferred_clear_preserves_output_if_no_next_output():
    messages=[message('protocol','iopub','stream',{'name':'stdout','text':'visible'}),
              message('protocol','iopub','clear_output',{'wait':True})]
    converted=notebook_result({'execution_id':'protocol','reply':{'status':'ok','execution_count':1},'messages':messages})
    assert converted['outputs'][0]['text']=='visible'


async def test_uses_existing_verified_owned_kernel(store):
    doc=code_notebook(store); execution,client=service(store)
    client.kernels.add('existing'); client._owned=client.kernels
    client.release.set()
    receipt=await execution.submit(nbid(doc),doc['cells'][0]['id'],rev(doc))
    await asyncio.gather(*tuple(execution.tasks.values()))
    assert store.execution(nbid(doc),receipt['execution_id'])['kernel_id']=='existing'
    assert client.kernels=={'existing'}
    await execution.close()


async def test_multiple_kernels_require_explicit_identity(store):
    doc=code_notebook(store); execution,client=service(store)
    client.kernels.update({'first','second'}); client._owned=client.kernels
    receipt=await execution.submit(nbid(doc),doc['cells'][0]['id'],rev(doc))
    await asyncio.gather(*tuple(execution.tasks.values()))
    assert store.execution(nbid(doc),receipt['execution_id'])['state']=='uncertain'
    assert client.calls==0
    await execution.close()


def test_display_updates_do_not_cross_restarted_kernel_sessions(store):
    doc=code_notebook(store); notebook=nbid(doc); first=doc['cells'][0]['id']
    receipt,_=store.begin(notebook,first,rev(doc))
    store.update_execution(notebook,receipt['execution_id'],runtime_id='runtime',generation=1,kernel_id='kernel')
    original=result(nbformat.v4.new_output('display_data',data={'text/plain':'old'}))
    original['display_map']={'display':[0]}
    store.finish(notebook,receipt['execution_id'],original)
    doc=store.open(notebook); doc=store.edit_cell(notebook,rev(doc),'insert',text='update')
    receipt,_=store.begin(notebook,doc['cells'][1]['id'],rev(doc))
    store.update_execution(notebook,receipt['execution_id'],runtime_id='runtime',generation=1,kernel_id='kernel')
    update=result(); update['kernel_session']='restarted'; update['display_updates']={'display':{'data':{'text/plain':'wrong'},'metadata':{}}}
    store.finish(notebook,receipt['execution_id'],update)
    assert store.open(notebook)['cells'][0]['outputs'][0]['data']['text/plain']=='old'
