"""Real read-only workspace browse requests against disposable directories."""
import os
from application.store import ApplicationStore
from notebooks.store import NotebookStore

import httpx
import nbformat
import pytest

from main import create_app


@pytest.fixture
def workspace(tmp_path):
    app = create_app(workspace_root=tmp_path)
    yield app, tmp_path
    app.state.workspace.close()


async def browse(app, path="", **params):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://europa") as client:
        return await client.get("/api/workspace", params={"path": path, **params})


async def read_text(app, path):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://europa") as client:
        return await client.get('/api/workspace/text', params={'path': path})


async def post(app, route, body):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://europa') as client:
        return await client.post('/api/workspace/' + route, json=body)


async def inspect_project(app, path):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://europa') as client:
        return await client.get('/api/workspace/project', params={'path': path})


@pytest.fixture
def writable(tmp_path):
    root = tmp_path / 'root'; root.mkdir()
    nb = NotebookStore(tmp_path / 'private-documents')
    journal = ApplicationStore(tmp_path / 'private-application', nb)
    app = create_app(workspace_root=root)
    app.state.application_store = journal
    from workspace.mutation import WorkspaceMutation
    app.state.workspace_mutation = WorkspaceMutation(app.state.workspace, journal)
    yield app, root, journal
    app.state.workspace.close(); journal.close(); nb.close()


@pytest.mark.anyio
async def test_project_creation_is_real_bound_and_copy_is_not_identity(writable):
    app, root, journal = writable
    body = {'operation_id': 'new-project', 'path': 'Work'}
    created = await post(app, 'projects', body)
    assert created.status_code == 201, created.text
    assert created.json()['path'] == 'Work'
    descriptor = root / 'Work' / '.europa-project.json'
    assert descriptor.is_file()
    assert (await inspect_project(app, 'Work')).json() == created.json()
    assert (await inspect_project(app, 'Work/.europa-project.json')).json() == created.json()
    assert (await post(app, 'projects', body)).json() == created.json()
    journal.close(); journal.__init__(root.parent / 'private-application', journal.notebooks)
    from workspace.mutation import WorkspaceMutation
    app.state.workspace_mutation = WorkspaceMutation(app.state.workspace, journal)
    assert (await inspect_project(app, 'Work')).json() == created.json()
    (root / 'Impostor').mkdir()
    (root / 'Impostor' / '.europa-project.json').write_bytes(descriptor.read_bytes())
    assert (await inspect_project(app, 'Impostor')).status_code == 404
    assert (await browse(app)).json()['items'][0]['type'] == 'directory'
    assert (await inspect_project(app, 'Work/../Impostor')).status_code == 400
    descriptor.rename(root / 'Work' / 'renamed.json')
    assert (await inspect_project(app, 'Work')).status_code == 404
    descriptor.write_bytes((root / 'Impostor' / '.europa-project.json').read_bytes())
    assert (await inspect_project(app, 'Work')).status_code == 404
    with journal.transaction():
        assert journal.db.execute('SELECT count(*) FROM project_bindings').fetchone()[0] == 1


@pytest.mark.anyio
@pytest.mark.parametrize('stage', [
    'after_intent', 'after_mkdir', 'after_descriptor', 'before_binding',
])
async def test_project_crash_stages_recover_conservatively(writable, tmp_path, monkeypatch, stage):
    from workspace.mutation import WorkspaceMutation
    app, root, journal = writable
    mutation = app.state.workspace_mutation
    def fail(at):
        if at == stage:
            raise RuntimeError('interrupted')
    monkeypatch.setattr(mutation, '_checkpoint', fail)
    body = {'operation_id': 'project-' + stage, 'path': 'Work'}
    with pytest.raises(RuntimeError, match='interrupted'):
        await post(app, 'projects', body)
    assert (await inspect_project(app, 'Work')).status_code == 404
    journal.close(); journal.__init__(tmp_path / 'private-application', journal.notebooks)
    app.state.workspace_mutation = WorkspaceMutation(app.state.workspace, journal)
    result = await post(app, 'projects', body)
    if stage in ('after_intent', 'after_mkdir'):
        assert result.status_code == 409
        assert (await inspect_project(app, 'Work')).status_code == 404
    else:
        assert result.status_code == 201, result.text
        assert (await inspect_project(app, 'Work')).json() == result.json()


@pytest.mark.anyio
async def test_project_collision_and_renamed_directory_do_not_claim_project(writable):
    app, root, _ = writable
    (root / 'Taken').mkdir()
    assert (await post(app, 'projects', {'operation_id': 'collision', 'path': 'Taken'})).status_code == 409
    assert not (root / 'Taken' / '.europa-project.json').exists()
    assert (await post(app, 'projects', {'operation_id': 'create', 'path': 'Original'})).status_code == 201
    (root / 'Original').rename(root / 'Moved')
    assert (await inspect_project(app, 'Moved')).status_code == 404
    assert (await inspect_project(app, 'Original')).status_code == 404


@pytest.mark.anyio
async def test_real_create_save_replay_conflict_and_external_change(writable):
    app, root, _ = writable
    folder = {'operation_id': 'folder-1', 'path': 'Notes'}
    created = await post(app, 'folders', folder)
    assert created.status_code == 201, created.text
    assert (root / 'Notes').is_dir()
    assert (await post(app, 'folders', folder)).json() == created.json()
    file = {'operation_id': 'file-1', 'path': 'Notes/日本語.md'}
    assert (await post(app, 'files', file)).status_code == 201
    assert (root / 'Notes' / '日本語.md').read_bytes() == b''
    first = (await read_text(app, file['path'])).json()
    save = {'operation_id': 'save-1', 'path': file['path'], 'revision': first['revision'], 'content': '世界'}
    written = await post(app, 'text/save', save)
    assert written.status_code == 200, written.text
    assert written.json()['revision'] == (await read_text(app, file['path'])).json()['revision']
    (root / 'Notes' / '日本語.md').write_text('external')
    assert (await post(app, 'text/save', save)).json() == written.json()
    assert (await post(app, 'text/save', {**save, 'operation_id': 'save-2'})).status_code == 409
    assert (await post(app, 'text/save', {**save, 'content': 'different'})).status_code == 409
    assert (root / 'Notes' / '日本語.md').read_text() == 'external'


@pytest.mark.anyio
@pytest.mark.parametrize('name', ['real.ipynb', 'REAL.IPYNB'])
async def test_text_save_rejects_existing_notebook_without_intent_or_byte_change(writable, name):
    app, root, journal = writable
    file = root / name
    nbformat.write(nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell('print(42)')]), file)
    before = file.read_bytes()
    observed = await read_text(app, name)
    assert observed.status_code == 200
    body = {'operation_id': 'notebook-save-' + name, 'path': name,
            'revision': observed.json()['revision'], 'content': 'overwritten'}
    result = await post(app, 'text/save', body)
    assert result.status_code == 400, result.text
    assert file.read_bytes() == before
    with journal.transaction():
        assert journal.db.execute('SELECT 1 FROM workspace_operations WHERE id=?', (body['operation_id'],)).fetchone() is None


@pytest.mark.anyio
async def test_pending_before_effect_uncertain_after_effect_reconciles_after_restart(writable, tmp_path, monkeypatch):
    from workspace.mutation import WorkspaceMutation
    app, root, journal = writable
    mutation = WorkspaceMutation(app.state.workspace, journal)
    app.state.workspace_mutation = mutation
    def fail(stage):
        if stage == 'after_intent': raise RuntimeError('injected')
    monkeypatch.setattr(mutation, '_checkpoint', fail)
    body = {'operation_id': 'no-effect', 'path': 'a.txt'}
    with pytest.raises(RuntimeError, match='injected'):
        await post(app, 'files', body)
    monkeypatch.setattr(mutation, '_checkpoint', lambda stage: None)
    assert (await post(app, 'files', body)).status_code == 409
    assert not (root / 'a.txt').exists()
    assert (await post(app, 'files', {**body, 'path': 'b.txt'})).status_code == 409
    body = {'operation_id': 'effect', 'path': 'b.txt'}
    monkeypatch.setattr(mutation, '_checkpoint', lambda stage: fail_effect(stage))
    def fail_effect(stage):
        if stage == 'after_effect': raise RuntimeError('injected')
    with pytest.raises(RuntimeError, match='injected'):
        await post(app, 'files', body)
    journal.close()
    journal.__init__(tmp_path / 'private-application', journal.notebooks)
    app.state.workspace_mutation = WorkspaceMutation(app.state.workspace, journal)
    result = await post(app, 'files', body)
    assert result.status_code == 201 and (root / 'b.txt').read_bytes() == b''
    assert (await post(app, 'files', body)).json() == result.json()


@pytest.mark.anyio
async def test_save_pending_reconciliation_and_symlink_rejected(writable, tmp_path, monkeypatch):
    from workspace.mutation import WorkspaceMutation
    app, root, journal = writable
    (root / 'note.txt').write_text('old')
    revision = (await read_text(app, 'note.txt')).json()['revision']
    mutation = WorkspaceMutation(app.state.workspace, journal)
    app.state.workspace_mutation = mutation
    def interrupt(stage):
        if stage == 'after_effect': raise RuntimeError('injected')
    monkeypatch.setattr(mutation, '_checkpoint', interrupt)
    body = {'operation_id': 'save-lost', 'path': 'note.txt', 'revision': revision, 'content': 'new'}
    with pytest.raises(RuntimeError, match='injected'):
        await post(app, 'text/save', body)
    journal.close(); journal.__init__(tmp_path / 'private-application', journal.notebooks)
    app.state.workspace_mutation = WorkspaceMutation(app.state.workspace, journal)
    result = await post(app, 'text/save', body)
    assert result.status_code == 200 and result.json()['content'] == 'new'
    assert (await post(app, 'text/save', body)).json() == result.json()
    (root / 'link.txt').symlink_to(root / 'note.txt')
    assert (await post(app, 'text/save', {**body, 'operation_id': 'link', 'path': 'link.txt'})).status_code in (400, 409)
    assert (root / 'note.txt').read_text() == 'new'


@pytest.mark.anyio
async def test_uncertain_folder_effect_never_auto_repeats_and_recovery_keeps_external_data(writable, monkeypatch):
    app, root, _ = writable
    mutation = app.state.workspace_mutation
    def interrupt(stage):
        if stage == 'after_effect': raise RuntimeError('injected')
    monkeypatch.setattr(mutation, '_checkpoint', interrupt)
    body = {'operation_id': 'folder-lost', 'path': 'newdir'}
    with pytest.raises(RuntimeError, match='injected'):
        await post(app, 'folders', body)
    monkeypatch.setattr(mutation, '_checkpoint', lambda stage: None)
    assert (await post(app, 'folders', body)).status_code == 409
    assert (root / 'newdir').is_dir()
    (root / 'newdir' / 'original.txt').write_text('untouched')
    assert (await post(app, 'folders', {**body, 'operation_id': 'another'})).status_code == 409
    assert (root / 'newdir' / 'original.txt').read_text() == 'untouched'


@pytest.mark.anyio
async def test_save_pending_before_effect_never_blindly_retries_and_external_swap_rejected(writable, monkeypatch):
    app, root, _ = writable
    target = root / 'existing.txt'; target.write_text('old')
    original = (await read_text(app, 'existing.txt')).json()['revision']
    mutation = app.state.workspace_mutation
    def stop(stage):
        if stage == 'after_intent': raise RuntimeError('injected')
    monkeypatch.setattr(mutation, '_checkpoint', stop)
    body = {'operation_id': 'intent-only', 'path': 'existing.txt', 'revision': original, 'content': 'new'}
    with pytest.raises(RuntimeError, match='injected'):
        await post(app, 'text/save', body)
    monkeypatch.setattr(mutation, '_checkpoint', lambda stage: None)
    assert (await post(app, 'text/save', body)).status_code == 409
    assert target.read_text() == 'old'
    def replace(stage):
        if stage == 'after_intent':
            replacement = root / 'replacement.txt'; replacement.write_text('external')
            os.replace(replacement, target)
    monkeypatch.setattr(mutation, '_checkpoint', replace)
    assert (await post(app, 'text/save', {**body, 'operation_id': 'swap'})).status_code == 409
    assert target.read_text() == 'external'


@pytest.mark.anyio
async def test_operation_is_pinned_to_original_root_and_no_journal_means_no_write(writable, tmp_path):
    app, root, _ = writable
    request = {'operation_id': 'root-bound', 'path': 'created.txt'}
    assert (await post(app, 'files', request)).status_code == 201
    other = tmp_path / 'other'; other.mkdir()
    from workspace.browser import WorkspaceBrowser
    from workspace.mutation import WorkspaceMutation
    alternate = WorkspaceBrowser(other)
    try:
        app.state.workspace_mutation = WorkspaceMutation(alternate, app.state.application_store)
        assert (await post(app, 'files', request)).status_code == 409
        assert not (other / 'created.txt').exists()
        del app.state.workspace_mutation
        assert (await post(app, 'files', {'operation_id': 'absent', 'path': 'ignored.txt'})).status_code == 503
        assert not (root / 'ignored.txt').exists()
    finally:
        alternate.close()


@pytest.mark.anyio
async def test_invalid_names_collisions_and_symlink_parents_do_not_touch_outside(writable, tmp_path):
    app, root, _ = writable
    outside = tmp_path / 'outside'; outside.mkdir()
    (root / 'shortcut').symlink_to(outside, target_is_directory=True)
    for index, path in enumerate(('shortcut/no.txt', '../outside/no.txt', '.hidden', 'a//b', 'bad.ipynb')):
        result = await post(app, 'files', {'operation_id': f'bad-{index}', 'path': path})
        assert result.status_code in (400, 404), (path, result.text)
    assert not list(outside.iterdir())
    (root / 'existing.txt').write_text('data')
    assert (await post(app, 'files', {'operation_id': 'collide', 'path': 'existing.txt'})).status_code == 409
    assert (root / 'existing.txt').read_text() == 'data'




@pytest.mark.anyio
async def test_text_read_observes_external_changes_without_writing(workspace):
    app, root = workspace
    file = root / 'code.py'
    file.write_text('print(1)\n', encoding='utf-8')
    first = await read_text(app, 'code.py')
    assert first.status_code == 200
    assert first.json()['content'] == 'print(1)\n'
    assert first.json()['path'] == 'code.py'
    assert first.json()['revision'] and str(root) not in first.text
    file.write_text('print(2)\n', encoding='utf-8')
    second = await read_text(app, 'code.py')
    assert second.status_code == 200
    assert second.json()['content'] == 'print(2)\n'
    assert second.json()['revision'] != first.json()['revision']
    assert file.read_text() == 'print(2)\n'


@pytest.mark.anyio
async def test_text_read_rejects_unsafe_content_and_paths(workspace):
    app, root = workspace
    (root / 'dir').mkdir()
    (root / 'dir' / 'note.md').write_text('こんにちは')
    assert (await read_text(app, 'dir/note.md')).json()['content'] == 'こんにちは'
    (root / 'binary.txt').write_bytes(b'\xff\x00')
    (root / 'huge.txt').write_bytes(b'x' * (1024 * 1024 + 1))
    (root / '.private').write_text('hidden')
    (root / 'symlink.txt').symlink_to(root / 'binary.txt')
    os.link(root / 'binary.txt', root / 'hardlink.txt')
    for path in ('binary.txt', 'huge.txt', '.private', 'symlink.txt', 'hardlink.txt', 'dir', '../other', ''):
        response = await read_text(app, path)
        assert response.status_code in (400, 404, 413, 415), (path, response.text)


@pytest.mark.anyio
async def test_text_read_pinned_root_after_path_replaced(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'safe.txt').write_text('safe')
    app = create_app(workspace_root=root)
    root.rename(tmp_path / 'moved')
    root.symlink_to(tmp_path.parent, target_is_directory=True)
    try:
        response = await read_text(app, 'safe.txt')
        assert response.status_code == 200
        assert response.json()['content'] == 'safe'
        assert str(tmp_path) not in response.text
    finally:
        app.state.workspace.close()


@pytest.mark.anyio
async def test_text_read_boundary_restart_and_external_replacement(workspace):
    app, root = workspace
    file = root / 'boundary.txt'
    file.write_bytes(b'x' * (1024 * 1024))
    original = await read_text(app, 'boundary.txt')
    assert original.status_code == 200
    assert len(original.json()['content']) == 1024 * 1024
    restarted = create_app(workspace_root=root)
    try:
        assert (await read_text(restarted, 'boundary.txt')).json()['revision'] == original.json()['revision']
        (root / 'replacement.txt').write_text('new')
        os.replace(root / 'replacement.txt', file)
        changed = await read_text(restarted, 'boundary.txt')
        assert changed.json()['content'] == 'new'
        assert changed.json()['revision'] != original.json()['revision']
        file.unlink()
        assert (await read_text(restarted, 'boundary.txt')).status_code == 404
    finally:
        restarted.state.workspace.close()


@pytest.mark.anyio
async def test_text_read_permission_failures_and_no_mutation_routes(workspace):
    app, root = workspace
    file = root / 'readonly.txt'
    file.write_text('untouched')
    file.chmod(0)
    try:
        assert (await read_text(app, 'readonly.txt')).status_code == 403
    finally:
        file.chmod(0o600)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://europa') as client:
        for method in ('post', 'put', 'patch'):
            response = await getattr(client, method)('/api/workspace/text', json={
                'path': 'readonly.txt', 'content': 'changed', 'operation_id': 'retry-1', 'revision': 'stale',
            })
            assert response.status_code in (404, 405)
    assert file.read_text() == 'untouched'


@pytest.mark.anyio
async def test_empty_workspace_has_no_cursor_or_runtime_side_effects(workspace):
    app, root = workspace
    result = await browse(app)
    assert result.status_code == 200
    assert result.json() == {'path': '', 'items': [], 'next_cursor': None}
    assert list(root.iterdir()) == []


@pytest.mark.anyio
async def test_root_and_nested_files_are_real_and_no_host_paths(workspace):
    app, root = workspace
    (root / "日本語").mkdir()
    (root / "日本語" / "existing.py").write_text("print(42)")
    (root / ".git").mkdir()
    (root / ".secret").write_text("private")
    empty = await browse(app)
    assert empty.status_code == 200
    assert empty.json()["items"] == [{"name": "日本語", "type": "directory", "size": None, "modified": pytest.approx((root / '日本語').stat().st_mtime)}]
    assert str(root) not in empty.text and '.secret' not in empty.text
    nested = await browse(app, "日本語")
    assert nested.status_code == 200
    assert nested.json()["items"][0]["name"] == "existing.py"
    assert nested.json()["items"][0]["type"] == "file"
    assert nested.json()["items"][0]["size"] == 9
    assert str(root) not in nested.text
    assert (await browse(app, "日本語/existing.py")).status_code == 400


@pytest.mark.anyio
async def test_rejects_escapes_symlinks_hardlinks_and_invalid_segments(workspace):
    app, root = workspace
    outside = root.parent / 'outside-workspace.txt'
    outside.write_text('outside')
    try:
        (root / 'escape').symlink_to(root.parent, target_is_directory=True)
        (root / 'linked').symlink_to(outside)
        os.link(outside, root / 'hardlinked')
        assert (await browse(app)).json()["items"] == []
        assert (await browse(app, "escape")).status_code == 400
        for path in ("..", "../outside-workspace.txt", "/etc", "a//b", "./x", "linked", "hardlinked", "a\x00b", "%2e%2e", "%2fetc"):
            response = await browse(app, path)
            assert response.status_code in (400, 422), (path, response.text)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://europa") as client:
            assert (await client.get('/api/workspace?path=%2e%2e%2fetc')).status_code in (400, 422)
    finally:
        outside.unlink()


@pytest.mark.anyio
async def test_pagination_is_bounded_deterministic_and_cursor_validated(workspace):
    app, root = workspace
    for index in reversed(range(105)):
        (root / f"file-{index:03}.txt").write_text("x")
    first = (await browse(app)).json()
    assert len(first['items']) == 100
    assert first['items'][0]['name'] == 'file-000.txt'
    assert first['items'][-1]['name'] == 'file-099.txt'
    assert first['next_cursor'] == 'file-099.txt'
    second = (await browse(app, cursor=first['next_cursor'])).json()
    assert [entry['name'] for entry in second['items']] == [f'file-{i:03}.txt' for i in range(100, 105)]
    assert second['next_cursor'] is None
    assert (await browse(app, cursor='../x')).status_code in (400, 422)
    assert (await browse(app, limit=101)).status_code == 422


@pytest.mark.anyio
async def test_missing_or_unreadable_directory_fails_without_fallback(workspace):
    app, root = workspace
    assert (await browse(app, 'missing')).status_code == 404
    (root / 'restricted').mkdir()
    (root / 'restricted').chmod(0)
    try:
        assert (await browse(app, 'restricted')).status_code == 403
        assert 'restricted' not in (await browse(app)).text
    finally:
        (root / 'restricted').chmod(0o700)


@pytest.mark.anyio
async def test_root_descriptor_stays_pinned_after_root_is_replaced(tmp_path):
    root = tmp_path / 'original'
    root.mkdir()
    (root / 'safe.txt').write_text('safe')
    app = create_app(workspace_root=root)
    root.rename(tmp_path / 'moved')
    root.symlink_to(tmp_path.parent, target_is_directory=True)
    try:
        result = await browse(app)
        assert result.status_code == 200
        assert [entry['name'] for entry in result.json()['items']] == ['safe.txt']
        assert str(tmp_path.parent) not in result.text
    finally:
        app.state.workspace.close()
