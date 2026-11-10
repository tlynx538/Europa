"""Canonical physical notebooks through the unchanged document/receipt APIs."""
import json
import os

import httpx
import nbformat
import pytest

from application.store import ApplicationStore
from main import create_app
from notebooks.store import NotebookError
from workspace.mutation import WorkspaceMutation
from workspace.notebooks import WorkspaceNotebookStore


@pytest.fixture
def physical(tmp_path):
    root = tmp_path / 'workspace'; root.mkdir()
    app = create_app(workspace_root=root)
    store = WorkspaceNotebookStore(tmp_path / 'private', app.state.workspace)
    journal = ApplicationStore(tmp_path / 'application', store)
    app.state.notebook_store = store
    app.state.application_store = journal
    app.state.workspace_mutation = WorkspaceMutation(app.state.workspace, journal)
    yield app, root, store, journal
    journal.close(); store.close(); app.state.workspace.close()


async def call(app, method, path, **kwargs):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://europa') as http:
        return await getattr(http, method)('/api/' + path, **kwargs)


async def associate(app, path, operation='open', create=False):
    return await call(app, 'post', 'workspace/notebooks' + ('' if create else '/open'), json={'path': path, 'operation_id': operation})


@pytest.mark.anyio
async def test_physical_create_project_association_save_receipts_and_restart(physical, tmp_path):
    app, root, store, journal = physical
    project = await call(app, 'post', 'workspace/projects', json={'path': 'Analytics', 'operation_id': 'project'})
    assert project.status_code == 201
    (root / 'Analytics' / 'Experiments').mkdir()
    created = await associate(app, 'Analytics/Experiments/Revenue.ipynb', 'create', True)
    assert created.status_code == 201, created.text
    nid = created.json()['notebook_id']
    assert created.json()['project_id'] == project.json()['id']
    assert not (tmp_path / 'private' / (nid + '.ipynb')).exists()
    target = root / 'Analytics/Experiments/Revenue.ipynb'
    assert json.loads(target.read_text())['metadata']['europa']['notebook_id'] == nid
    project_doc = (await call(app, 'get', 'projects/' + project.json()['id'])).json()
    assert project_doc['data']['notebooks'] == [{'notebook_id': nid, 'present': True}]
    conversation = (await call(app, 'get', f'notebooks/{nid}/conversation')).json()['id']
    cell_response = await call(app, 'post', f'notebooks/{nid}/cells', json={'revision': 1, 'source': 'print(42)'})
    assert cell_response.status_code == 201, cell_response.text
    cell = cell_response.json()['cells'][0]['id']
    assert (await call(app, 'patch', f'notebooks/{nid}/cells/{cell}', json={'revision': 1, 'source': 'stale'})).status_code == 409
    receipt, _ = store.begin(nid, cell, 2)
    store.finish(nid, receipt['execution_id'], {'status': 'ok', 'outputs': [{'output_type': 'stream', 'name': 'stdout', 'text': '42\n'}], 'execution_count': 1})
    receipt_bytes = (tmp_path / 'private' / f"{nid}.{receipt['execution_id']}.execution.json").read_bytes()
    revision = store.open(nid)['metadata']['europa']['revision']
    journal.close(); store.close()
    store.__init__(tmp_path / 'private', app.state.workspace)
    journal.__init__(tmp_path / 'application', store)
    assert (await associate(app, 'Analytics/Experiments/Revenue.ipynb', 'again')).json()['notebook_id'] == nid
    assert (await associate(app, 'Analytics/Experiments/Revenue.ipynb', 'create', True)).json()['notebook_id'] == nid
    assert store.open(nid)['metadata']['europa']['revision'] == revision
    assert store.open(nid)['cells'][0]['outputs'][0]['text'] == '42\n'
    assert (await call(app, 'get', f'notebooks/{nid}/conversation')).json()['id'] == conversation
    assert (tmp_path / 'private' / f"{nid}.{receipt['execution_id']}.execution.json").read_bytes() == receipt_bytes
    assert (await call(app, 'delete', f'notebooks/{nid}?revision={revision}')).status_code == 409
    assert target.is_file()


@pytest.mark.anyio
async def test_import_is_explicit_preserves_outputs_distrusts_copied_id_and_blocks_external_edits(physical):
    app, root, store, journal = physical
    old = store.create('Private')
    old_id = old['metadata']['europa']['notebook_id']
    imported = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell('6 * 7', outputs=[nbformat.v4.new_output('execute_result', data={'text/plain': '42'}, execution_count=1)])])
    imported.metadata.europa = old['metadata']['europa']
    target = root / 'Imported.ipynb'; nbformat.write(imported, target)
    raw = target.read_bytes()
    assert (await call(app, 'get', 'workspace')).status_code == 200
    assert target.read_bytes() == raw
    opened = await associate(app, 'Imported.ipynb')
    assert opened.status_code == 200, opened.text
    nid = opened.json()['notebook_id']
    assert nid != old_id
    assert ''.join(store.open(nid)['cells'][0]['outputs'][0]['data']['text/plain']) == '42'
    assert (await associate(app, 'Imported.ipynb', 'repeat')).json()['notebook_id'] == nid
    assert store.open(old_id) == old
    target.write_text('external content')
    assert (await call(app, 'get', f'notebooks/{nid}')).status_code == 409
    assert (await associate(app, 'Imported.ipynb', 'external')).status_code == 409
    assert (await call(app, 'post', f'notebooks/{nid}/cells', json={'revision': 1, 'source': 'overwrite'})).status_code == 409
    assert target.read_text() == 'external content'
    assert any(item['notebook_id'] == old_id for item in store.list())


@pytest.mark.anyio
@pytest.mark.parametrize('stage', ['after_intent', 'after_effect'])
async def test_physical_creation_and_save_reconcile_without_duplicate_identity(physical, monkeypatch, stage):
    app, root, store, journal = physical
    def fail(at):
        if at == stage: raise RuntimeError('injected')
    monkeypatch.setattr(store, '_checkpoint', fail)
    with pytest.raises(RuntimeError, match='injected'):
        await associate(app, 'Crash.ipynb', 'create', True)
    monkeypatch.setattr(store, '_checkpoint', lambda _: None)
    private = root.parent / 'private'
    store.close(); store.__init__(private, app.state.workspace)
    result = await associate(app, 'Crash.ipynb', 'create', True)
    assert result.status_code == 201, result.text
    nid = result.json()['notebook_id']
    assert len(store._manifest()) == 1

    monkeypatch.setattr(store, '_checkpoint', fail)
    with pytest.raises(RuntimeError, match='injected'):
        store.edit_cell(nid, 1, 'insert', text='print(1)')
    monkeypatch.setattr(store, '_checkpoint', lambda _: None)
    document = store.open(nid)
    assert document['metadata']['europa']['revision'] == (2 if stage == 'after_effect' else 1)
    assert len(document['cells']) == (1 if stage == 'after_effect' else 0)
    assert len(store._manifest()) == 1


@pytest.mark.anyio
async def test_private_paths_and_directory_replacement_block_workspace_access(physical):
    app, root, store, journal = physical
    private = root / 'private'; private.mkdir()
    (private / 'secret.txt').write_text('private')
    app.state.workspace.protect(private)
    listing = await call(app, 'get', 'workspace')
    assert 'private' not in listing.text
    assert (await call(app, 'get', 'workspace?path=private')).status_code == 403
    assert (await call(app, 'get', 'workspace/text?path=private/secret.txt')).status_code == 403
    assert (await associate(app, 'private/Hidden.ipynb', 'hidden', True)).status_code == 403
    (root / 'Original').mkdir()
    result = await associate(app, 'Original/Note.ipynb', 'safe', True)
    nid = result.json()['notebook_id']
    (root / 'Original').rename(root / 'Moved')
    (root / 'Original').mkdir()
    assert (await call(app, 'get', f'notebooks/{nid}')).status_code == 409
    assert not list((root / 'Original').iterdir())


@pytest.mark.anyio
async def test_browser_routes_refresh_and_physical_membership_cannot_be_forged(physical):
    app, root, store, journal = physical
    assert (await call(app, 'post', 'workspace/projects', json={'path': 'Work', 'operation_id': 'work'})).status_code == 201
    opened = await associate(app, 'Work/Notes.ipynb', 'note', True)
    nid = opened.json()['notebook_id']
    other = await call(app, 'post', 'projects', json={'operation_id': 'other', 'title': 'Other'})
    project = other.json()
    response = await call(app, 'post', f"projects/{project['id']}/notebooks", json={'operation_id': 'invalid', 'revision': project['revision'], 'notebook_id': nid})
    assert response.status_code == 409
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://europa') as http:
        assert (await http.get('/browse?path=Work')).status_code == 200
        assert (await http.get('/text?path=Work/README.md')).status_code == 200


@pytest.mark.anyio
async def test_paths_collisions_unsafe_files_and_operation_reuse(physical, tmp_path):
    app, root, store, journal = physical
    outside = tmp_path / 'outside'; outside.mkdir()
    (root / 'escape').symlink_to(outside)
    for path in ('../oops.ipynb', 'escape/oops.ipynb', '.secret.ipynb', 'not.txt'):
        response = await associate(app, path, path, True)
        assert response.status_code in (400, 404), response.text
    assert not list(outside.iterdir())
    (root / 'invalid.ipynb').write_text('broken')
    assert (await associate(app, 'invalid.ipynb')).status_code == 422
    assert (root / 'invalid.ipynb').read_text() == 'broken'
    assert (await associate(app, 'A.ipynb', 'unique', True)).status_code == 201
    assert (await associate(app, 'B.ipynb', 'unique', True)).status_code == 409
    assert (await associate(app, 'A.ipynb', 'other', True)).status_code == 409
    assert (await associate(app, 'B.ipynb', 'second', True)).status_code == 201
    assert (await associate(app, 'A.ipynb', 'second')).status_code == 409
    (root / 'Link.ipynb').symlink_to(root / 'A.ipynb')
    assert (await associate(app, 'Link.ipynb', 'link')).status_code == 400
    os.link(root / 'A.ipynb', root / 'Hard.ipynb')
    assert (await associate(app, 'Hard.ipynb', 'hard')).status_code == 400


@pytest.mark.anyio
async def test_first_open_ignores_read_induced_access_time_changes(physical):
    app, root, store, journal = physical
    path = root / 'First.ipynb'
    nbformat.write(nbformat.v4.new_notebook(), path)
    os.utime(path, ns=(0, path.stat().st_mtime_ns))
    response = await associate(app, 'First.ipynb', 'first-read')
    assert response.status_code == 200, response.text


@pytest.mark.anyio
async def test_completed_receipt_recovers_physical_commit_after_lost_ack(physical, monkeypatch):
    app, root, store, journal = physical
    response = await associate(app, 'Receipt.ipynb', 'receipt', True)
    nid = response.json()['notebook_id']
    document = store.edit_cell(nid, 1, 'insert', text='print(42)')
    receipt, _ = store.begin(nid, document['cells'][0]['id'], 2)
    def fail(stage):
        if stage == 'after_effect': raise RuntimeError('lost acknowledgement')
    monkeypatch.setattr(store, '_checkpoint', fail)
    with pytest.raises(RuntimeError, match='lost acknowledgement'):
        store.finish(nid, receipt['execution_id'], {'status': 'ok', 'outputs': [{'output_type': 'stream', 'name': 'stdout', 'text': '42\n'}], 'execution_count': 1})
    monkeypatch.setattr(store, '_checkpoint', lambda _: None)
    store.close(); store.__init__(root.parent / 'private', app.state.workspace)
    assert store.execution(nid, receipt['execution_id'])['state'] == 'applied'
    assert store.open(nid)['metadata']['europa']['revision'] == 4
    assert store.open(nid)['cells'][0]['outputs'][0]['text'] == '42\n'
