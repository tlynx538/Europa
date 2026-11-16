"""Phase 4C: Notebook Coordinator — context, planning approvals, revisions, isolation, scope and recovery."""
import json

import nbformat
import pytest
import pytest_asyncio

from agents.policy import ROLES, Budget, PolicyDenied, PolicyEngine
from application import models as m
from application.store import ApplicationError
from agent_support import Harness, call, calls, op, reply


@pytest_asyncio.fixture
async def make(tmp_path):
    created = []

    async def factory(script, **kwargs):
        harness = Harness(tmp_path, script, **kwargs)
        created.append(harness)
        return await harness.start()
    yield factory
    for harness in created:
        await harness.stop(); harness.close()


MARKOV = [
    {'key': 'matrix', 'title': 'Define a transition matrix', 'acceptance_criteria': ['Rows sum to 1']},
    {'key': 'simulate', 'title': 'Simulate transitions', 'acceptance_criteria': ['A state sequence is produced'], 'depends_on': ['matrix']},
    {'key': 'stationary', 'title': 'Estimate the stationary distribution', 'acceptance_criteria': ['Matches simulation'],
     'depends_on': ['simulate']},
    {'key': 'plot', 'title': 'Visualize convergence', 'acceptance_criteria': ['A convergence plot is shown'], 'depends_on': ['stationary']},
]
FORBIDDEN = ('read_notebook_summary', 'delegate_task', 'insert_code_cell', 'execute_cell')


def propose(tasks=MARKOV, objective='Understand this Markov chain', **extra):
    return call('propose_roadmap', objective=objective, tasks=tasks, **extra)


def context_of(h, index=0):
    system, user = h.provider.calls[index]['messages'][:2]
    return system['content'], user['content']


def owner_roadmap(h, project, notebook, tasks, objective='Owner plan'):
    return h.application.roadmap_create(m.RoadmapCreate(operation_id=op(), project_id=project, notebook_id=notebook,
                                                         objective=objective, tasks=[m.Task(**t) for t in tasks]))


def roadmaps(h, project):
    return h.application.listing('roadmap', project=project)['items']


# ------------------------------------------------------------------ policy

def test_notebook_role_has_no_delegation_and_default_budget():
    role = ROLES['notebook_coordinator']
    assert role.tools == ('read_notebook', 'read_roadmap', 'read_execution', 'propose_roadmap')
    assert 'delegate' not in role.capabilities and role.budget == Budget()
    run = {'role': 'notebook_coordinator', 'notebook_id': 'a' * 32, 'depth': 0}
    for name in FORBIDDEN:
        with pytest.raises(PolicyDenied):
            PolicyEngine().check(run, name)
    assert 'delegate_task' not in [t.name for t in PolicyEngine().tools_for('notebook_coordinator')]


# ------------------------------------------------------------------ 1. context

async def test_context_has_provenance_cells_receipt_output_history_and_project(make):
    h = await make([reply('It computes 42.')])
    nid, project, conversation = h.project_notebook(cells=['x = 41\nx + 1'])
    cell = h.notebooks.open(nid)['cells'][0]['id']
    revision = h.notebooks.open(nid)['metadata']['europa']['revision']
    receipt, _ = h.notebooks.begin(nid, cell, revision)
    h.notebooks.update_execution(nid, receipt['execution_id'], state='running', runtime_id='runtime-secret-id',
                                 kernel_id='kernel-secret-id', generation=7)
    output = nbformat.v4.new_output('execute_result', data={'text/plain': '42', 'text/html': '<b>html-only</b>'}, execution_count=1)
    applied = h.notebooks.finish(nid, receipt['execution_id'], {'status': 'ok', 'outputs': [output], 'execution_count': 1,
                                                                 'kernel_session': 'session-secret-id'})
    assert applied['state'] == 'applied'
    document = h.notebooks.open(nid)
    revision, last_cell = document['metadata']['europa']['revision'], document['cells'][-1]['id']
    earlier = 'Earlier question about the matrix ' + 'z' * 1200
    h.say(conversation, earlier, cell_id=cell)
    run = await h.ask(conversation, 'What does this notebook do so far?', cell_id=cell)
    assert (await h.settle(run['id']))['state'] == 'completed'
    system, user = context_of(h)
    assert f'<untrusted source="notebook:{nid}@{revision}">' in user
    assert 'x = 41' in user and '"output": "42"' in user and 'html-only' not in user
    assert 'Earlier question about the matrix' in user and f'[{len(earlier) - 1000} more characters omitted]' in user
    assert f'"cell_id": "{cell}"' in user and f'(The owner attached cell {cell}.)' in user
    assert '"title": "Analysis"' in user and '"status": "active"' in user and 'No roadmap yet.' in user
    for secret in ('runtime-secret-id', 'kernel-secret-id', 'session-secret-id', receipt['execution_id']):
        assert secret not in user and secret not in system
    for sentence in ('You cannot assign work, edit cells or run code in this phase.',
                     'Never state that a task is complete unless its roadmap status is completed.',
                     'When the notebook is not in a project, explain that planning needs a project.'):
        assert sentence in system
    assert 'delegate_task' not in system
    events = h.runtime.store.events(run['id'], 0, 50)['items']
    sources = next(e for e in events if e['type'] == 'context_assembled')['data']['sources']
    assert sources[:3] == [f'notebook:{nid}@{revision}', f'project:{project}', 'roadmap:none']
    answer = h.messages(conversation)[-1]['data']
    assert answer['author_role'] == 'coordinator' and answer['content'] == 'It computes 42.'
    assert answer['placement_after_cell_id'] == last_cell


async def test_context_limits_markers_agent_flag_and_roadmap_fields(make):
    h = await make([reply('ok')])
    nid, project, conversation = h.project_notebook(cells=[f'cell_{i} = {i}' for i in range(31)] + ['L' * 1600])
    revision = h.notebooks.open(nid)['metadata']['europa']['revision']
    h.notebooks.edit_cell(nid, revision, 'insert', text='agent_cell = 1',
                          metadata={'europa_agent': {'tool_call_id': 'x', 'run_id': 'y'}})
    owner_roadmap(h, project, nid, [
        {'id': 'first', 'title': 'Load data', 'acceptance_criteria': ['Loads'], 'status': 'blocked', 'explanation': 'Waiting for file',
         'evidence': [{'summary': f'evidence {i}'} for i in range(5)]},
        {'id': 'second', 'title': 'Plot', 'acceptance_criteria': ['Plot shown'], 'dependencies': ['first']}])
    for i in range(14):
        h.say(conversation, f'history message {i:02d}')
    run = await h.ask(conversation, 'Summarize')
    await h.settle(run['id'])
    _, user = context_of(h)
    assert '"cell_count": 33' in user and '"omitted_leading_cells": 3' in user
    assert 'cell_2 = 2' not in user and 'cell_3 = 3' in user
    assert '[100 more characters omitted]' in user and '"agent_authored": true' in user
    roadmap = json.loads(user.split('<untrusted source="roadmap">\n', 1)[1].split('\n</untrusted>', 1)[0])
    assert roadmap['revision'] == 1 and roadmap['objective'] == 'Owner plan' and roadmap['progress'] == 0.0
    first, second = roadmap['tasks']
    assert first['status'] == 'blocked' and first['explanation'] == 'Waiting for file'
    assert first['evidence'] == ['evidence 2', 'evidence 3', 'evidence 4'] and second['dependencies'] == ['first']
    assert second['acceptance_criteria'] == ['Plot shown']
    history = json.loads(user.split(f'<untrusted source="conversation:{conversation}">\n', 1)[1].split('\n</untrusted>', 1)[0])
    assert len(history) == 12 and history[0]['content'] == 'history message 03' and history[-1]['content'] == 'Summarize'
    assert len(user) <= Budget().context_chars + 7000  # context budget plus the bounded owner message section


# ------------------------------------------------------------------ 2. plan approval

async def test_plan_approval_creates_one_pending_roadmap_bound_to_the_notebook(make):
    h = await make([propose(), reply('Saved.')])
    nid, project, conversation = h.project_notebook()
    run = await h.ask(conversation, 'Plan how to understand this Markov chain.')
    assert (await h.settle(run['id']))['state'] == 'waiting_for_approval'
    approval = h.pending_approval(run['id'])
    preview = approval['preview']
    assert preview['title'] == 'Create roadmap' and preview['roadmap_revision'] is None and approval['revision'] is None
    assert [t['title'] for t in preview['tasks']] == [t['title'] for t in MARKOV]
    assert [t['depends_on'] for t in preview['tasks']] == [[], ['Define a transition matrix'], ['Simulate transitions'],
                                                           ['Estimate the stationary distribution']]
    assert roadmaps(h, project) == []
    await h.approve(run['id'])
    assert (await h.settle(run['id']))['state'] == 'completed'
    (roadmap,) = roadmaps(h, project)
    tasks = roadmap['data']['tasks']
    assert roadmap['notebook_id'] == nid and roadmap['revision'] == 1 and roadmap['data']['progress'] == 0.0
    assert [t['status'] for t in tasks] == ['pending'] * 4 and not any(t['accepted'] or t['evidence'] for t in tasks)
    assert [t['dependencies'] for t in tasks] == [[], [tasks[0]['id']], [tasks[1]['id']], [tasks[2]['id']]]
    assert all(t['links']['notebook_id'] == nid for t in tasks)
    assert [v['revision'] for v in h.application.versions(roadmap['id'])['items']] == [1]
    (tool_call,) = h.runtime.store.tool_calls(run['id'])
    assert tool_call['operation_id'] == 'agent-' + tool_call['id'] and tool_call['state'] == 'succeeded'


# ------------------------------------------------------------------ 3. revision

async def test_revision_rejects_status_and_preserves_task_state(make):
    revised = [{'key': 'own', 'title': 'Owner task renamed', 'acceptance_criteria': ['x']},
               {'key': 'compare', 'title': 'Compare with a 3-state chain', 'acceptance_criteria': ['Compared'], 'depends_on': ['own']}]
    forged = [{**revised[0], 'status': 'completed'}]
    h = await make([propose(forged), propose(revised), reply('Revised.')])
    nid, project, conversation = h.project_notebook()
    original = owner_roadmap(h, project, nid, [{'id': 'own', 'title': 'Owner task', 'acceptance_criteria': ['x'],
                                                 'status': 'in_progress', 'explanation': 'Halfway',
                                                 'evidence': [{'summary': 'owner note'}]}])
    run = await h.ask(conversation, 'Add a step to compare with a 3-state chain.')
    assert (await h.settle(run['id']))['state'] == 'waiting_for_approval'
    rejected, pending = h.runtime.store.tool_calls(run['id'])
    assert rejected['state'] == 'rejected' and rejected['result']['status'] == 'invalid'
    approval = h.pending_approval(run['id'])
    assert approval['preview']['title'] == 'Revise roadmap' and approval['preview']['roadmap_revision'] == 1
    assert approval['revision'] == 1 and approval['preview']['removed_task_ids'] == []
    await h.approve(run['id'])
    assert (await h.settle(run['id']))['state'] == 'completed'
    (roadmap,) = roadmaps(h, project)
    own, compare = roadmap['data']['tasks']
    assert roadmap['id'] == original['id'] and roadmap['revision'] == 2
    assert own['id'] == 'own' and own['title'] == 'Owner task renamed' and own['status'] == 'in_progress'
    assert own['explanation'] == 'Halfway' and [e['summary'] for e in own['evidence']] == ['owner note']
    assert compare['status'] == 'pending' and compare['dependencies'] == ['own'] and not compare['accepted']
    assert roadmap['data']['progress'] == original['data']['progress'] == 0.0


# ------------------------------------------------------------------ 4. stale plan, 5. removed tasks

async def test_stale_plan_is_a_conflict_and_keeps_the_owner_edit(make):
    h = await make([propose(), reply('The roadmap changed; I will re-read it.')])
    nid, project, conversation = h.project_notebook()
    original = owner_roadmap(h, project, nid, [{'id': 'own', 'title': 'Owner task', 'acceptance_criteria': ['x']}])
    run = await h.ask(conversation, 'Plan this')
    await h.settle(run['id'])
    assert h.pending_approval(run['id'])['preview']['roadmap_revision'] == 1
    h.application.roadmap_update(original['id'], m.RoadmapUpdate(operation_id=op(), revision=1, objective='Owner objective',
                                 tasks=[m.Task(id='own', title='Owner task', acceptance_criteria=['x'])]))
    await h.approve(run['id'])
    assert (await h.settle(run['id']))['state'] == 'completed'
    (tool_call,) = h.runtime.store.tool_calls(run['id'])
    assert tool_call['state'] == 'failed' and tool_call['result']['status'] == 'conflict'
    assert 'roadmap changed after this plan was proposed' in tool_call['result']['summary']
    current = h.application.get(original['id'], 'roadmap')
    assert current['revision'] == 2 and current['data']['objective'] == 'Owner objective'
    assert [t['id'] for t in current['data']['tasks']] == ['own']


async def test_removed_tasks_are_listed_in_the_preview(make):
    h = await make([propose([{'key': 'keep', 'title': 'Keep', 'acceptance_criteria': ['x']}])])
    nid, project, conversation = h.project_notebook()
    owner_roadmap(h, project, nid, [{'id': 'keep', 'title': 'Keep', 'acceptance_criteria': ['x']},
                                    {'id': 'drop', 'title': 'Drop', 'acceptance_criteria': ['y']}])
    run = await h.ask(conversation, 'Simplify the plan')
    await h.settle(run['id'])
    assert h.pending_approval(run['id'])['preview']['removed_task_ids'] == ['drop']


# ------------------------------------------------------------------ 6. isolation

async def test_notebook_role_cannot_delegate_edit_execute_or_read_other_notebooks(make):
    h = await make([])
    a, project, conversation = h.project_notebook(cells=['alpha_marker = 1'])
    b = h.notebooks.associate('Analysis/other.ipynb', op(), create=True)
    h.application.bind_physical_notebook(b, h.notebooks.location(b))
    h.notebooks.edit_cell(b, 1, 'insert', text='beta_marker = 2')
    owner_roadmap(h, project, a, [{'id': 'ta', 'title': 'Alpha task', 'acceptance_criteria': ['x']}], objective='Alpha objective')
    owner_roadmap(h, project, b, [{'id': 'tb', 'title': 'Beta task', 'acceptance_criteria': ['x']}], objective='Beta objective')
    h.provider.script = [
        calls(('read_notebook_summary', {'notebook_id': b}), ('delegate_task', {'task_id': 'ta', 'instructions': 'do it'}),
              ('insert_code_cell', {'source': 'print(1)', 'explanation': 'x'}), ('execute_cell', {'cell_id': 'x'})),
        call('read_roadmap'), call('read_notebook'), reply('Done reading.')]
    run = await h.ask(conversation, 'Read everything you can')
    assert (await h.settle(run['id']))['state'] == 'completed'
    results = {c['name']: c for c in h.runtime.store.tool_calls(run['id'])}
    for name in FORBIDDEN:
        assert results[name]['state'] == 'rejected' and results[name]['result']['status'] == 'policy'
    assert not h.runtime.store.approvals(run['id'])
    for sent in h.provider.calls:
        assert not set(FORBIDDEN) & set(sent['tools'])
    # Everything the harness supplied (system, context, tool results); the model's own
    # rejected call arguments naming B are excluded.
    supplied = json.dumps([x['content'] for x in h.provider.calls[-1]['messages'] if x['role'] != 'assistant'])
    assert 'alpha_marker' in supplied and 'Alpha objective' in supplied
    for marker in ('beta_marker', 'Beta objective', 'Beta task', b):
        assert marker not in supplied
    roadmap_result = json.loads(next(x['content'] for x in h.provider.calls[2]['messages']
                                     if x['role'] == 'tool' and 'Read roadmap' in x['content']))
    assert [t['task_id'] for t in roadmap_result['data']['roadmap']['tasks']] == ['ta']
    assert [c['source'] for c in h.notebooks.open(a)['cells']] == ['alpha_marker = 1']
    with h.application.bookkeeping() as db:
        assert db.execute("SELECT COUNT(*) FROM agent_runs WHERE role='coding_agent'").fetchone()[0] == 0


async def test_roadmap_selection_is_latest_for_this_notebook_beyond_one_page(make):
    h = await make([call('read_roadmap'), reply('ok')])
    a, project, conversation = h.project_notebook()
    b = h.notebooks.associate('Analysis/other.ipynb', op(), create=True)
    h.application.bind_physical_notebook(b, h.notebooks.location(b))
    owner_roadmap(h, project, a, [{'id': 'old', 'title': 'Old', 'acceptance_criteria': ['x']}], objective='Older A plan')
    for i in range(100):
        owner_roadmap(h, project, b, [{'id': f'b{i}', 'title': 'B', 'acceptance_criteria': ['x']}], objective=f'B plan {i}')
    newest = owner_roadmap(h, project, a, [{'id': 'new', 'title': 'New', 'acceptance_criteria': ['x']}], objective='Newest A plan')
    run = await h.ask(conversation, 'status?')
    assert (await h.settle(run['id']))['state'] == 'completed'
    _, user = context_of(h)
    assert 'Newest A plan' in user and 'Older A plan' not in user and 'B plan' not in user
    (tool_call,) = h.runtime.store.tool_calls(run['id'])
    assert tool_call['result']['data']['roadmap']['roadmap_id'] == newest['id']


# ------------------------------------------------------------------ 7. standalone

async def test_standalone_notebook_cannot_plan(make):
    h = await make([propose(), reply('Planning needs a project.')])
    nid, conversation = h.standalone_notebook()
    run = await h.ask(conversation, 'Plan this notebook')
    assert (await h.settle(run['id']))['state'] == 'completed'
    (tool_call,) = h.runtime.store.tool_calls(run['id'])
    assert tool_call['state'] == 'rejected' and tool_call['result']['status'] == 'policy'
    assert not h.runtime.store.approvals(run['id'])
    _, user = context_of(h)
    assert 'Project: none' in user and f'notebook:{nid}@' in user
    assert h.application.listing('roadmap')['items'] == []
    assert h.messages(conversation)[-1]['data']['author_role'] == 'coordinator'


# ------------------------------------------------------------------ 8. restart

async def test_waiting_plan_survives_restart_and_completes(make):
    h = await make([propose()])
    nid, project, conversation = h.project_notebook()
    run = await h.ask(conversation, 'Plan it')
    await h.settle(run['id'])
    await h.restart([reply('Saved after restart.')])
    assert h.application.ensure_notebook_conversation(nid)['id'] == conversation
    (listed,) = (await h.http('get', f'agent/runs?conversation_id={conversation}')).json()['items']
    assert listed['id'] == run['id'] and listed['state'] == 'waiting_for_approval'
    (approval,) = listed['approvals']
    assert approval['tool'] == 'propose_roadmap' and approval['state'] == 'pending' and approval['preview']['tasks']
    decided = await h.http('post', f"agent/approvals/{approval['id']}",
                           json={'operation_id': op(), 'decision': 'approve', 'digest': approval['digest']})
    assert decided.status_code == 200
    assert (await h.settle(run['id']))['state'] == 'completed'
    events = h.events(run['id'])
    assert events[:4] == ['run_queued', 'run_started', 'context_assembled', 'approval_requested'] and events[-1] == 'reply_posted'
    assert len(roadmaps(h, project)) == 1
    assert h.messages(conversation)[-1]['data']['content'] == 'Saved after restart.'


# ------------------------------------------------------------------ 9. HTTP scope

async def test_http_scope_checks_for_notebook_runs(make):
    h = await make([reply('Hello.')])
    a, project, conversation = h.project_notebook()
    b, other = h.standalone_notebook('Other')
    legacy = h.application.conversation_create(m.ConversationCreate(operation_id=op(), project_id=project, title='legacy'))
    legacy_message = h.say(legacy['id'], 'old thread')
    response = await h.http('post', 'agent/runs', json={'operation_id': op(), 'conversation_id': legacy['id'],
                                                         'message_id': legacy_message['id']})
    assert response.status_code == 409
    foreign = h.say(other, 'from notebook B')
    response = await h.http('post', 'agent/runs', json={'operation_id': op(), 'conversation_id': conversation, 'message_id': foreign['id']})
    assert response.status_code == 422
    agent = h.application.message_append(conversation, m.MessageCreate(operation_id=op(), content='agent says'), author_role='coordinator')
    response = await h.http('post', 'agent/runs', json={'operation_id': op(), 'conversation_id': conversation, 'message_id': agent['id']})
    assert response.status_code == 422
    with h.application.bookkeeping() as db:
        assert db.execute('SELECT COUNT(*) FROM agent_runs').fetchone()[0] == 0
    message = h.say(conversation, 'hi')
    started = await h.http('post', 'agent/runs', json={'operation_id': op(), 'conversation_id': conversation, 'message_id': message['id']})
    assert started.status_code == 201
    body = started.json()
    assert body['role'] == 'notebook_coordinator' and body['notebook_id'] == a and body['project_id'] == project


# ------------------------------------------------------------------ 10. cancel

async def test_cancel_waiting_plan_invalidates_approval_and_saves_nothing(make):
    h = await make([propose()])
    nid, project, conversation = h.project_notebook()
    run = await h.ask(conversation, 'Plan it')
    await h.settle(run['id'])
    approval = h.pending_approval(run['id'])
    assert (await h.runtime.cancel(run['id']))['state'] == 'cancelled'
    assert h.runtime.store.approvals(run['id'])[0]['state'] == 'invalidated'
    with pytest.raises(ApplicationError):
        await h.runtime.decide(approval['id'], operation_id=op(), decision='approve', digest_value=approval['digest'])
    await h.runtime.idle()
    assert roadmaps(h, project) == [] and 'run_cancelled' in h.events(run['id'])
