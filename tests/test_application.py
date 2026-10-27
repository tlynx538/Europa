"""Application persistence, concurrency, history and HTTP contracts."""
import concurrent.futures
import copy
import json
import os
import signal
import sqlite3
import subprocess
import sys
import uuid

import nbformat
import pytest
from pydantic import ValidationError

from application import models as m
from application.store import ApplicationStore, ApplicationError
from notebooks.store import NotebookStore, NotebookError, source_hash


def op(): return uuid.uuid4().hex


@pytest.fixture
def state(tmp_path):
    notebooks = NotebookStore(tmp_path / 'documents')
    application = ApplicationStore(tmp_path / 'application', notebooks)
    yield application, notebooks
    application.close(); notebooks.close()


def project(app): return app.project_create(m.ProjectCreate(operation_id=op(), title='Research'))


def member(app, nb, project_id=None):
    p = project(app) if project_id is None else app.get(project_id, 'project')
    doc = nb.create('Existing')
    doc = nb.edit_cell(doc['metadata']['europa']['notebook_id'], 1, 'insert', text='print(42)')
    nid = doc['metadata']['europa']['notebook_id']
    p = app.membership(p['id'], m.Membership(operation_id=op(), revision=p['revision'], notebook_id=nid))
    return p, doc


def conversation(app, p, notebook=None):
    return app.conversation_create(m.ConversationCreate(operation_id=op(), project_id=p['id'], notebook_id=notebook))


def task(identity='step', **kwargs):
    return m.Task(id=identity, title=identity, acceptance_criteria=['Owner verifies expected result'], **kwargs)


def roadmap(app, p, tasks=None, notebook=None):
    return app.roadmap_create(m.RoadmapCreate(operation_id=op(), project_id=p['id'], notebook_id=notebook,
                                             objective='Answer the research question', tasks=tasks or []))


def test_existing_notebook_migration_and_restart_without_rewrite(state, tmp_path):
    app, nb = state
    doc = nb.create(); nid = doc['metadata']['europa']['notebook_id']
    doc = nb.edit_cell(nid, 1, 'insert', text='42')
    doc['cells'][0]['outputs'] = [nbformat.v4.new_output('display_data', data={'text/html':'<script>bad()</script>', 'application/json':{'a':1}})]
    doc = nb.save(nid, 2, doc)
    before = (tmp_path / 'documents' / (nid + '.ipynb')).read_bytes()
    app.close()
    app.__init__(tmp_path / 'application', nb)
    p = project(app)
    app.membership(p['id'], m.Membership(operation_id=op(), revision=1, notebook_id=nid))
    assert (tmp_path / 'documents' / (nid + '.ipynb')).read_bytes() == before
    app.close(); app.__init__(tmp_path / 'application', nb)
    assert app.get(p['id'], 'project')['data']['notebooks'] == [{'notebook_id':nid, 'present':1}]
    assert nb.open(nid) == doc


def test_membership_revision_exclusive_and_disassociation(state):
    app, nb = state; p, doc = member(app, nb); nid = doc['metadata']['europa']['notebook_id']
    other = project(app)
    with pytest.raises(ApplicationError): app.membership(other['id'], m.Membership(operation_id=op(), revision=1, notebook_id=nid))
    with pytest.raises(ApplicationError): app.membership(p['id'], m.Membership(operation_id=op(), revision=1, notebook_id=nid))
    app.membership(p['id'], m.Membership(operation_id=op(), revision=2, notebook_id=nid, associated=False))
    app.membership(other['id'], m.Membership(operation_id=op(), revision=1, notebook_id=nid))
    assert nb.open(nid) == doc


@pytest.mark.parametrize('status', ['archived', 'deleted'])
def test_project_lifecycle_keeps_documents_history_and_no_runtime_calls(state, status):
    app, nb = state; p, doc = member(app, nb); c = conversation(app, p)
    app.project_update(p['id'], m.ProjectUpdate(operation_id=op(), revision=2, title='History', status=status))
    assert nb.open(doc['metadata']['europa']['notebook_id']) == doc
    assert app.get(c['id'], 'conversation')['id'] == c['id']
    with pytest.raises(ApplicationError): conversation(app, p)
    assert not hasattr(app, 'supervisor')


def test_message_concurrency_order_pagination_and_retry(state):
    app, _ = state; p = project(app); c = conversation(app, p)
    bodies = [m.MessageCreate(operation_id=op(), content=str(i)) for i in range(24)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(lambda b: app.message_append(c['id'], b), bodies * 2))
    assert len({r['id'] for r in records}) == 24
    seen, cursor = [], 0
    while True:
        page = app.listing('message', parent=c['id'], after=cursor, limit=7)
        seen.extend(page['items'])
        if page['next_cursor'] is None: break
        cursor = page['next_cursor']
    assert len(seen) == 24
    assert [r['sequence'] for r in seen] == sorted(r['sequence'] for r in seen)
    assert all(r['data']['author_role'] == 'user' for r in seen)
    with pytest.raises(ApplicationError): app.message_append(c['id'], bodies[0].model_copy(update={'content':'changed'}))
    with pytest.raises(sqlite3.IntegrityError):
        with app.transaction(): app.db.execute('UPDATE records SET revision=2 WHERE id=?', (seen[0]['id'],))


def test_message_reference_validation(state):
    app, nb = state; p, doc = member(app, nb); nid = doc['metadata']['europa']['notebook_id']
    c = conversation(app, p, nid); other = conversation(app, p)
    first = app.message_append(c['id'], m.MessageCreate(operation_id=op(), content='Question'))
    with pytest.raises(ApplicationError): app.message_append(other['id'], m.MessageCreate(operation_id=op(), content='wrong', parent_id=first['id']))
    with pytest.raises(NotebookError): app.message_append(c['id'], m.MessageCreate(operation_id=op(), content='wrong', cell_id='unknown'))
    with pytest.raises(ApplicationError): app.message_append(c['id'], m.MessageCreate(operation_id=op(), content='wrong', document_revision=999))


@pytest.mark.parametrize('tasks', [
    [task('a', dependencies=['missing'])],
    [task('a', dependencies=['b']), task('b', dependencies=['a'])],
    [task('a', parent_id='b'), task('b', parent_id='a')],
    [task('a'), task('a')],
])
def test_invalid_plans_are_atomic(state, tasks):
    app, _ = state; p = project(app)
    with pytest.raises(ApplicationError): roadmap(app, p, tasks)
    assert app.listing('roadmap', project=p['id'])['items'] == []


def test_roadmap_versions_progress_evidence_and_revision(state):
    app, _ = state; p = project(app)
    done = task('a', status='completed', accepted=True, evidence=[m.Evidence(summary='Owner inspected the saved result')])
    r = roadmap(app, p, [done, task('b', dependencies=['a'], status='in_progress')])
    assert r['data']['progress'] == 50
    assert r['data']['tasks'][0]['display'] == {'compact':True, 'checkmark':True, 'show_progress':False}
    assert r['data']['tasks'][1]['display']['show_progress']
    body = m.RoadmapUpdate(operation_id=op(), revision=1, objective='Changed requirements', tasks=[done, task('b', status='cancelled')])
    updated = app.roadmap_update(r['id'], body)
    assert updated['revision'] == 2 and updated['data']['progress'] == 100
    assert app.roadmap_update(r['id'], body) == updated
    with pytest.raises(ApplicationError): app.roadmap_update(r['id'], body.model_copy(update={'operation_id':op()}))
    assert [v['revision'] for v in app.versions(r['id'])['items']] == [1, 2]
    assert app.versions(r['id'])['items'][0]['data']['objective'] == r['data']['objective']


@pytest.mark.parametrize('kwargs', [dict(status='completed'), dict(status='completed',accepted=True), dict(status='blocked')])
def test_completion_and_blocking_require_evidence(kwargs):
    with pytest.raises(ValidationError): task(**kwargs)


def test_dependencies_must_complete_and_parent_progress(state):
    app, _ = state; p = project(app)
    done = task('done', parent_id='parent', status='completed', accepted=True, evidence=[m.Evidence(summary='Reviewed')])
    r = roadmap(app, p, [task('parent', status='in_progress'), done, task('todo', parent_id='parent', weight=3)])
    assert r['data']['progress'] == 20 and r['data']['tasks'][0]['progress'] == 20
    with pytest.raises(ApplicationError):
        roadmap(app, p, [task('x'), task('y', dependencies=['x'],status='completed',accepted=True,evidence=[m.Evidence(summary='No')])])
    assert roadmap(app, p, [])['data']['progress'] == 0


def test_task_links_execution_evidence_and_selection(state):
    app, nb = state; p, doc = member(app, nb); nid = doc['metadata']['europa']['notebook_id']; cid = doc['cells'][0]['id']
    receipt, _ = nb.begin(nid, cid, 2)
    result = {'status':'ok','outputs':[],'execution_count':1}
    nb.finish(nid, receipt['execution_id'], result)
    c = conversation(app, p, nid)
    message = app.message_append(c['id'], m.MessageCreate(operation_id=op(),content='Reviewed successful result',cell_id=cid))
    t = task('analysis',status='completed',accepted=True,
             links=m.Links(notebook_id=nid,cell_id=cid,execution_receipt_id=receipt['execution_id']),
             evidence=[m.Evidence(summary='Reviewed output',message_id=message['id'],execution_receipt_id=receipt['execution_id'])])
    r = roadmap(app,p,[t],nid)
    msg = app.message_append(c['id'],m.MessageCreate(operation_id=op(),content='Task follow-up',task_id=t.id))
    assert msg['data']['task_id'] == t.id and r['data']['progress'] == 100


def test_comment_move_edit_delete_and_thread_resolution(state):
    app, nb = state; p, doc = member(app, nb); nid = doc['metadata']['europa']['notebook_id']; cid = doc['cells'][0]['id']
    body = m.CommentCreate(operation_id=op(),project_id=p['id'],notebook_id=nid,cell_id=cid,code_range=m.CodeRange(start=0,end=5),content='Why?')
    c = app.comment_create(nid,body)
    assert c['data']['anchor_status'] == 'current'
    doc = nb.edit_cell(nid,2,'insert',text='other')
    doc = nb.edit_cell(nid,3,'move',cid,index=1)
    assert app.get(c['id'],'comment')['data']['anchor_status'] == 'current'
    reply = app.comment_create(nid,body.model_copy(update={'operation_id':op(),'parent_id':c['id'],'content':'Reply'}))
    app.resolve(c['id'],m.Resolve(operation_id=op(),revision=1,resolved=True))
    assert app.get(reply['id'],'comment')['data']['resolved']
    nb.edit_cell(nid,4,'edit',cid,text='different')
    changed = app.get(c['id'],'comment')
    assert changed['data']['anchor_status'] == 'outdated' and changed['data']['source_fingerprint'] == c['data']['source_fingerprint']
    nb.edit_cell(nid,5,'delete',cid)
    assert app.get(c['id'],'comment')['data']['anchor_status'] == 'orphaned'
    nb.delete(nid,6)
    assert app.get(p['id'],'project')['data']['notebooks'][0]['present'] == 0
    assert app.get(reply['id'],'comment')['data']['anchor_status'] == 'orphaned'


def test_comment_rejects_stale_ranges(state):
    app, nb = state; p,doc = member(app,nb); nid=doc['metadata']['europa']['notebook_id']; cid=doc['cells'][0]['id']
    base=dict(operation_id=op(),project_id=p['id'],notebook_id=nid,cell_id=cid,content='Why')
    for fields in [dict(document_revision=1),dict(source_fingerprint='0'*64),dict(code_range=m.CodeRange(start=0,end=999))]:
        with pytest.raises(ApplicationError): app.comment_create(nid,m.CommentCreate(**base,**fields))


def test_events_are_append_only_ordered_and_idempotent(state):
    app,_=state; p=project(app)
    body=m.ActivityCreate(operation_id=op(),project_id=p['id'],summary='<b>Changed</b>\x00 requirements')
    event=app.activity_append(p['id'],body)
    assert event==app.activity_append(p['id'],body)
    assert event['data']['summary']=='Changed requirements' and event['data']['actor']=='user'
    other=app.activity_append(p['id'],body.model_copy(update={'operation_id':op()}))
    assert other['sequence']>event['sequence']
    with pytest.raises(sqlite3.IntegrityError):
        with app.transaction(): app.db.execute('DELETE FROM records WHERE id=?',(event['id'],))


def test_interrupted_transaction_rolls_back_and_retry_survives_restart(state,tmp_path,monkeypatch):
    app,nb=state; body=m.ProjectCreate(operation_id=op(),title='Atomic')
    original=app._insert
    def fail(*args,**kwargs): original(*args,**kwargs); raise OSError('interruption')
    monkeypatch.setattr(app,'_insert',fail)
    with pytest.raises(OSError): app.project_create(body)
    assert app.listing('project')['items']==[]
    monkeypatch.setattr(app,'_insert',original)
    p=app.project_create(body)
    app.close(); app.__init__(tmp_path/'application',nb)
    assert app.project_create(body)==p


def test_sigkill_open_transaction_recovery(state,tmp_path):
    app,nb=state; p=project(app); app.close()
    marker=tmp_path/'ready'
    program='''import sqlite3,sys,time
from pathlib import Path
c=sqlite3.connect(sys.argv[1]); c.execute("BEGIN IMMEDIATE")
c.execute("UPDATE records SET revision=999 WHERE kind='project'")
Path(sys.argv[2]).write_text('ready')
time.sleep(20)
'''
    process=subprocess.Popen([sys.executable,'-c',program,str(tmp_path/'application/application.sqlite3'),str(marker)])
    import time
    try:
        deadline=time.monotonic()+5
        while not marker.exists() and time.monotonic()<deadline: time.sleep(.01)
        assert marker.exists()
        process.kill(); process.wait(timeout=5)
        app.__init__(tmp_path/'application',nb)
        assert app.get(p['id'],'project')['revision']==1
    finally:
        if process.poll() is None: process.kill(); process.wait(timeout=5)


def test_private_permissions_foreign_key_integrity_and_schema_version(state,tmp_path):
    app,nb=state
    assert (tmp_path/'application').stat().st_mode & 0o777 == 0o700
    for name in ['application.sqlite3','application.sqlite3-wal','application.sqlite3-shm']:
        assert (tmp_path/'application'/name).stat().st_mode & 0o077 == 0
    with pytest.raises(sqlite3.IntegrityError):
        with app.transaction(): app.db.execute("INSERT INTO memberships VALUES ('missing','missing',1)")
    app.close()
    db=sqlite3.connect(tmp_path/'application/application.sqlite3'); db.execute('PRAGMA user_version=99'); db.close()
    with pytest.raises(ApplicationError): app.__init__(tmp_path/'application',nb)


@pytest.mark.parametrize('file_kind',['symlink','hardlink','public'])
def test_reject_unsafe_database(tmp_path,file_kind):
    nb=NotebookStore(tmp_path/'docs'); root=tmp_path/'app'; root.mkdir(mode=0o700)
    external=tmp_path/'external'; external.write_text('private'); external.chmod(0o600)
    target=root/'application.sqlite3'
    if file_kind=='symlink': target.symlink_to(external)
    elif file_kind=='hardlink': os.link(external,target)
    else: target.write_text('public'); target.chmod(0o644)
    try:
        with pytest.raises((NotebookError,OSError)): ApplicationStore(root,nb)
    finally: nb.close()


@pytest.mark.parametrize('receipt_state',['prepared','running','completed','uncertain','conflict','applied'])
def test_receipt_retention_never_prunes_old_or_referenced_receipts(state,tmp_path,receipt_state):
    app,nb=state; p,doc=member(app,nb); nid=doc['metadata']['europa']['notebook_id']; cid=doc['cells'][0]['id']
    receipt,_=nb.begin(nid,cid,2)
    nb.finish(nid,receipt['execution_id'],{'status':'ok','outputs':[],'execution_count':1})
    nb.update_execution(nid,receipt['execution_id'],state=receipt_state,created_at=0)
    nb.delete(nid,4)
    app.close(); app.__init__(tmp_path/'application',nb)
    nb.close(); nb.__init__(tmp_path/'documents')
    assert nb.execution(nid,receipt['execution_id'])['created_at']==0


@pytest.mark.parametrize('mime,value',[
    ('text/plain','<script>text</script>'),('text/html','<img src="https://example.test/x" onerror="bad()">'),
    ('image/svg+xml','<svg onload="bad()"/>'),('image/png','aGVsbG8='),
    ('application/json',{'untrusted':'<script>'}),('application/javascript','bad()'),
])
def test_untrusted_mime_preserved_with_rendering_contract(state,mime,value):
    app,nb=state; p,doc=member(app,nb); nid=doc['metadata']['europa']['notebook_id']
    doc['cells'][0]['outputs']=[nbformat.v4.new_output('display_data',data={mime:value})]
    saved=nb.save(nid,2,doc)
    assert saved['cells'][0]['outputs'][0]['data'][mime]==value
    from pathlib import Path
    contract=(Path(__file__).parents[1]/'docs/phase3b-safety-review.md').read_text()
    assert 'Never execute output-provided JavaScript' in contract


async def test_http_contracts_roles_conflicts_and_no_execution(state):
    import httpx
    from main import create_app
    app,nb=state; server=create_app(); server.state.application_store=app; server.state.notebook_store=nb
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server),base_url='http://europa') as http:
        p=(await http.post('/api/projects',json={'operation_id':op(),'title':'HTTP'})).json()
        c=(await http.post('/api/conversations',json={'operation_id':op(),'project_id':p['id']})).json()
        route=f"/api/conversations/{c['id']}/messages"
        assert (await http.post(route,json={'operation_id':op(),'content':'forged','author_role':'coordinator'})).status_code==422
        assert (await http.post(route,json={'operation_id':op(),'content':'print(42)'})).status_code==201
        assert len((await http.get(route,params={'limit':1})).json()['items'])==1
        assert (await http.get(route,params={'limit':101})).status_code==422
        assert (await http.get('/api/projects/missing')).status_code==404
        assert (await http.patch('/api/projects/'+p['id'],json={'operation_id':op(),'revision':99,'title':'stale'})).status_code==409
        assert (await http.post(f"/api/projects/{p['id']}/activities",json={'operation_id':op(),'project_id':p['id'],'actor':'coordinator','event_type':'task_completed','summary':'forged'})).status_code==422
    assert not hasattr(server.state,'supervisor')


@pytest.mark.parametrize('role', ['coordinator','coding_agent','system/tool'])
def test_trusted_server_authorship_is_separate_from_public_requests(state,role):
    app,_=state; p=project(app); c=conversation(app,p)
    msg=app.message_append(c['id'],m.MessageCreate(operation_id=op(),content='Public activity outcome'),author_role=role)
    assert msg['data']['author_role']==role
    event=app.activity_append(p['id'],m.ActivityCreate(operation_id=op(),project_id=p['id'],event_type='task_created',summary='Task recorded'),actor=role)
    assert event['data']['actor']==role and event['data']['event_type']=='task_created'


def test_comment_reply_after_cell_deletion_and_roadmap_historical_links(state):
    app,nb=state; p,doc=member(app,nb); nid=doc['metadata']['europa']['notebook_id']; cid=doc['cells'][0]['id']
    c=app.comment_create(nid,m.CommentCreate(operation_id=op(),project_id=p['id'],notebook_id=nid,cell_id=cid,content='Original'))
    t=task('historical',links=m.Links(notebook_id=nid,cell_id=cid))
    r=roadmap(app,p,[t],nid)
    nb.edit_cell(nid,2,'delete',cid)
    reply=app.comment_create(nid,m.CommentCreate(operation_id=op(),project_id=p['id'],notebook_id=nid,cell_id=cid,parent_id=c['id'],content='Historical reply'))
    assert reply['data']['anchor_status']=='orphaned'
    assert reply['data']['source_fingerprint']==c['data']['source_fingerprint']
    updated=app.roadmap_update(r['id'],m.RoadmapUpdate(operation_id=op(),revision=1,objective='Updated goal',tasks=[t]))
    assert updated['revision']==2
    nb.delete(nid,3)
    app.membership(p['id'],m.Membership(operation_id=op(),revision=2,notebook_id=nid,associated=False))
    assert app.get(p['id'],'project')['data']['notebooks']==[]


def test_roadmap_concurrent_revision_only_one_commits(state):
    app,_=state; p=project(app); r=roadmap(app,p,[task()])
    def update(i):
        try:
            return app.roadmap_update(r['id'],m.RoadmapUpdate(operation_id=op(),revision=1,objective=str(i),tasks=[task()]))
        except ApplicationError as exc: return exc.status_code
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(update,range(2)))
    assert sum(isinstance(v,dict) for v in results)==1 and 409 in results
    assert len(app.versions(r['id'])['items'])==2


async def test_http_roadmap_comments_tombstone_and_typed_schema(state):
    import httpx
    from main import create_app
    app,nb=state; p,doc=member(app,nb); nid=doc['metadata']['europa']['notebook_id']; cid=doc['cells'][0]['id']
    server=create_app(); server.state.application_store=app; server.state.notebook_store=nb
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server),base_url='http://europa') as http:
        response=await http.post('/api/roadmaps',json=m.RoadmapCreate(operation_id=op(),project_id=p['id'],notebook_id=nid,objective='Goal',tasks=[task()]).model_dump())
        assert response.status_code==201,response.text
        r=response.json()
        assert (await http.get(f"/api/roadmaps/{r['id']}/tasks")).status_code==200
        assert (await http.get(f"/api/roadmaps/{r['id']}/versions")).json()['items'][0]['data']['tasks'][0]['id']=='step'
        response=await http.post(f'/api/notebooks/{nid}/comments',json=m.CommentCreate(operation_id=op(),project_id=p['id'],notebook_id=nid,cell_id=cid,content='Question').model_dump())
        assert response.status_code==201,response.text
        comment=response.json()
        response=await http.patch(f"/api/comments/{comment['id']}",json={'operation_id':op(),'revision':1,'resolved':True})
        assert response.status_code==200 and response.json()['data']['resolved']
        assert (await http.get(f"/api/comments/{comment['id']}")).status_code==200
        assert (await http.post(f"/api/projects/{p['id']}/activities",json={'operation_id':op(),'project_id':p['id'],'event_type':'task_completed','summary':'Forged'})).status_code==403
        body={'operation_id':op(),'revision':2}
        response=await http.request('DELETE',f"/api/projects/{p['id']}",json=body)
        assert response.status_code==200 and response.json()['data']['status']=='deleted'
        assert (await http.request('DELETE',f"/api/projects/{p['id']}",json=body)).json()==response.json()
        schema=(await http.get('/openapi.json')).json()
        assert 'RoadmapData' in schema['components']['schemas'] and 'CommentData' in schema['components']['schemas']


def test_cross_project_message_links_and_unaccepted_execution_evidence_rejected(state):
    app,nb=state; p,doc=member(app,nb); nid=doc['metadata']['europa']['notebook_id']; cid=doc['cells'][0]['id']
    other=project(app); c=conversation(app,other)
    msg=app.message_append(c['id'],m.MessageCreate(operation_id=op(),content='Other project'))
    with pytest.raises(ApplicationError): roadmap(app,p,[task(links=m.Links(message_id=msg['id']))])
    receipt,_=nb.begin(nid,cid,2)
    candidate=task(status='completed',accepted=True,links=m.Links(notebook_id=nid,cell_id=cid),
        evidence=[m.Evidence(summary='Not verified',execution_receipt_id=receipt['execution_id'])])
    with pytest.raises(ApplicationError): roadmap(app,p,[candidate],nid)


def test_parent_acceptance_prevents_premature_100_percent(state):
    app,_=state; p=project(app)
    r=roadmap(app,p,[task('parent',status='in_progress'),task('child',parent_id='parent',status='completed',accepted=True,evidence=[m.Evidence(summary='Reviewed')])])
    assert r['data']['progress']==50
    assert r['data']['tasks'][0]['progress']==50


def test_task_identity_cannot_be_reused_in_another_roadmap_history(state):
    app,_=state; p=project(app); r=roadmap(app,p,[task('stable')])
    app.roadmap_update(r['id'],m.RoadmapUpdate(operation_id=op(),revision=1,objective='Changed scope',tasks=[]))
    with pytest.raises(ApplicationError): roadmap(app,p,[task('stable')])


def test_deleted_project_can_explicitly_release_membership_without_losing_history(state):
    app,nb=state; p,doc=member(app,nb); nid=doc['metadata']['europa']['notebook_id']; c=conversation(app,p,nid)
    deleted=app.project_delete(p['id'],m.Revision(operation_id=op(),revision=2))
    app.membership(p['id'],m.Membership(operation_id=op(),revision=deleted['revision'],notebook_id=nid,associated=False))
    other=project(app)
    app.membership(other['id'],m.Membership(operation_id=op(),revision=1,notebook_id=nid))
    assert app.get(c['id'],'conversation')['project_id']==p['id']
    assert nb.open(nid)==doc
