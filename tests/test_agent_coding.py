"""Phase 4D Stage 2: supervised Coding Agent — owner and Coordinator assignment, approvals,
own-cell execution, deterministic execution IDs, reconciliation, the repair rule and reports."""
import asyncio
import hashlib

import pytest
import pytest_asyncio

from agents.demo import DemoProvider, task_code
from agents.providers import ModelResponse
from agents.store import AgentStore
from agents.tools import NOT_OWN
from application import models as m
from notebooks.service import NotebookService
from notebooks.store import NotebookError, NotebookStore
from agent_support import Harness, call, calls, op, reply

MATRIX_CODE = task_code('Define a transition matrix')
OK_OUTPUT = [{'output_type': 'stream', 'name': 'stdout', 'text': 'A: row sum = 1.0\nB: row sum = 1.0\n'}]
ERROR_OUTPUT = [{'output_type': 'error', 'ename': 'ZeroDivisionError', 'evalue': 'division by zero', 'traceback': []}]
TASKS = [
    {'id': 't1', 'title': 'Define a transition matrix', 'acceptance_criteria': ['Rows sum to 1']},
    {'id': 't2', 'title': 'Simulate transitions', 'acceptance_criteria': ['A sequence is produced'], 'dependencies': ['t1']},
    {'id': 't3', 'title': 'Write notes', 'acceptance_criteria': ['Notes exist']},
]


class FakeService:
    """Creates real receipts through NotebookStore.begin/finish without a kernel.
    Modes: ok | error | lost (finish, then the acknowledgement is lost) | running (never
    settles) | refuse (raises before begin)."""

    def __init__(self, store, modes=()):
        self.store, self.modes, self.begun = store, list(modes), []

    async def submit(self, notebook, cell, revision, *, kernel_id=None, timeout=30, execution_id=None):
        mode = self.modes.pop(0) if self.modes else 'ok'
        if mode == 'refuse':
            raise NotebookError('Notebook already has an active document execution', 409)
        receipt, _ = await asyncio.to_thread(self.store.begin, notebook, cell, revision, execution_id)
        self.begun.append(receipt['execution_id'])
        if mode == 'running':
            await asyncio.to_thread(self.store.update_execution, notebook, receipt['execution_id'], state='running')
            return receipt
        error = mode == 'error'
        await asyncio.to_thread(self.store.finish, notebook, receipt['execution_id'], {
            'status': 'error' if error else 'ok', 'outputs': ERROR_OUTPUT if error else OK_OUTPUT, 'execution_count': 1})
        if mode == 'lost':
            raise ConnectionError('acknowledgement lost')
        return receipt


class Roles:
    """Role-dispatching fake script: coordinator and coder queues. A callable item receives the
    last tool result (dict or None) and returns a ModelResponse."""

    def __init__(self, coordinator=(), coder=()):
        self.coordinator, self.coder = list(coordinator), list(coder)

    def __call__(self, messages, tools):
        system = messages[0]['content']
        queue = self.coder if 'Role: Coding Agent' in system else self.coordinator
        if not queue:
            return reply('(script exhausted)')
        item = queue.pop(0)
        if callable(item):
            import json
            last = json.loads(messages[-1]['content']) if messages[-1]['role'] == 'tool' else None
            return item(last)
        return item


def execute_inserted(last):
    return call('execute_cell', cell_id=last['data']['cell_id'], explanation='Run it')


def insert(source=MATRIX_CODE):
    return call('insert_code_cell', source=source, explanation='Matrix and row sums')


@pytest_asyncio.fixture
async def make(tmp_path):
    created = []

    async def factory(roles=None, modes=(), provider=None, **kwargs):
        harness = Harness(tmp_path, provider or roles or Roles(), **kwargs)
        harness.service = FakeService(harness.notebooks, modes)
        harness.app.state.notebook_service = harness.service
        created.append(harness)
        return await harness.start()
    yield factory
    for harness in created:
        await harness.stop(); harness.close()


def setup(h, tasks=TASKS):
    nid, project, conversation = h.project_notebook(cells=['owner_cell = 1'])
    roadmap = h.application.roadmap_create(m.RoadmapCreate(operation_id=op(), project_id=project, notebook_id=nid,
                                                           objective='Understand a Markov chain', tasks=[m.Task(**t) for t in tasks]))
    return nid, project, conversation, roadmap


async def assign(h, roadmap, task_id='t1', operation=None, **extra):
    return await h.http('post', 'agent/tasks/assign', json={'operation_id': operation or op(), 'roadmap_id': roadmap['id'],
                                                            'task_id': task_id, **extra})


def coding_runs(h):
    with h.application.bookkeeping() as db:
        ids = [r['id'] for r in db.execute("SELECT id FROM agent_runs WHERE role='coding_agent' ORDER BY seq")]
    return [h.runtime.store.get_run(i) for i in ids]


def task(h, roadmap, task_id):
    return next(t for t in h.application.get(roadmap['id'], 'roadmap')['data']['tasks'] if t['id'] == task_id)


async def approve_all(h, run_id, limit=10):
    for _ in range(limit):
        state = (await h.settle(run_id))['state']
        if state != 'waiting_for_approval': return state
        await h.approve(run_id)
    raise AssertionError('too many approvals')


# ------------------------------------------------------------------ 1. full flow

async def test_full_flow_owner_assign_insert_execute_report_and_evidence(make):
    h = await make(Roles(coder=[insert(), execute_inserted, reply('Defined the matrix; rows sum to 1.')]))
    nid, project, conversation, roadmap = setup(h)
    operation = op()
    response = await assign(h, roadmap, operation=operation)
    assert response.status_code == 201, response.text
    run = response.json()
    assert (run['role'], run['parent_run_id'], run['depth'], run['notebook_id'], run['project_id']) == ('coding_agent', None, 1, nid, project)
    assert run['task'] == {'roadmap_id': roadmap['id'], 'task_id': 't1', 'title': 'Define a transition matrix', 'assigned_by': 'user'}
    messages = h.messages(conversation)
    assert [(x['data']['author_role'], x['data']['content']) for x in messages] == [
        ('system/tool', 'Owner assigned "Define a transition matrix" to the Coding Agent.')]
    assert run['trigger_message_id'] == messages[0]['id'] and run['conversation_id'] == conversation
    replay = await assign(h, roadmap, operation=operation)
    assert replay.status_code == 201 and replay.json()['id'] == run['id'] and len(h.messages(conversation)) == 1
    assert task(h, roadmap, 't1')['status'] == 'in_progress'

    assert (await h.settle(run['id']))['state'] == 'waiting_for_approval'
    approval = h.pending_approval(run['id'])
    assert approval['tool'] == 'insert_code_cell' and approval['preview']['source'] == MATRIX_CODE
    assert [c['source'] for c in h.notebooks.open(nid)['cells']] == ['owner_cell = 1']  # nothing inserted before approval
    await h.approve(run['id'])
    assert (await h.settle(run['id']))['state'] == 'waiting_for_approval'
    cells = h.notebooks.open(nid)['cells']
    assert [c['source'] for c in cells] == ['owner_cell = 1', MATRIX_CODE]
    inserted = cells[1]
    assert inserted['metadata']['europa_agent']['run_id'] == run['id']
    execute = h.pending_approval(run['id'])
    assert execute['tool'] == 'execute_cell' and execute['preview']['cell_id'] == inserted['id']
    assert 'Not a sandbox' in execute['preview']['warning']
    assert h.notebooks.executions(nid) == [] and h.service.begun == []  # modification is not execution
    await h.approve(run['id'])
    assert (await h.settle(run['id']))['state'] == 'completed'
    execute_call = next(c for c in h.runtime.store.tool_calls(run['id']) if c['name'] == 'execute_cell')
    (receipt,) = h.notebooks.executions(nid)
    assert receipt['execution_id'] == execute_call['id'] and receipt['state'] == 'applied'
    report = h.messages(conversation)[-1]['data']
    assert report['author_role'] == 'coding_agent'
    assert (report['execution_receipt_id'], report['cell_id'], report['task_id']) == (receipt['execution_id'], inserted['id'], 't1')
    assert report['content'].startswith('Defined the matrix; rows sum to 1.\n\nExecution record: receipt ')
    assert 'applied, status ok.\nObserved output:\nA: row sum = 1.0\nB: row sum = 1.0' in report['content']
    evidence = task(h, roadmap, 't1')['evidence']
    assert len(evidence) == 1 and evidence[0]['execution_receipt_id'] == receipt['execution_id']
    assert evidence[0]['message_id'] == h.messages(conversation)[-1]['id']
    assert task(h, roadmap, 't1')['status'] == 'in_progress'  # acceptance stays with the owner
    listed = (await h.http('get', f'agent/runs?conversation_id={conversation}')).json()['items']
    assert [r['id'] for r in listed] == [run['id']] and listed[0]['trigger_message_id'] == messages[0]['id']


# ------------------------------------------------------------------ 2. readiness rules

async def test_dependency_and_single_active_run_are_enforced_for_assign_and_delegate(make):
    roles = Roles(coordinator=[call('delegate_task', task_id='t2', instructions='go'), call('delegate_task', task_id='t3', instructions='go'),
                               reply('Not possible now.')], coder=[insert()])
    h = await make(roles)
    nid, project, conversation, roadmap = setup(h)
    blocked = await assign(h, roadmap, 't2')
    assert blocked.status_code == 409 and 'depends on unfinished tasks: t1' in blocked.json()['detail']
    first = await assign(h, roadmap, 't1')
    assert first.status_code == 201
    second = await assign(h, roadmap, 't3')
    assert second.status_code == 409 and 'already active' in second.json()['detail']
    assert (await h.settle(first.json()['id']))['state'] == 'waiting_for_approval'  # still active
    run = await h.ask(conversation, 'Assign more work')
    assert (await h.settle(run['id']))['state'] == 'completed'
    delegated = h.runtime.store.tool_calls(run['id'])
    assert [(c['state'], c['result']['status']) for c in delegated] == [('rejected', 'conflict'), ('rejected', 'conflict')]
    assert 'depends on unfinished tasks' in delegated[0]['result']['summary'] and 'already active' in delegated[1]['result']['summary']
    assert not h.runtime.store.approvals(run['id']) and len(coding_runs(h)) == 1
    # Only one assignment message was posted (for the run that started).
    assert [x['data']['author_role'] for x in h.messages(conversation)].count('system/tool') == 1


async def test_concurrent_assignments_start_exactly_one_run(make):
    h = await make()
    nid, project, conversation, roadmap = setup(h)
    responses = await asyncio.gather(assign(h, roadmap, 't1'), assign(h, roadmap, 't3'), assign(h, roadmap, 't1'))
    assert sorted(r.status_code for r in responses) == [201, 409, 409]
    assert len(coding_runs(h)) == 1


async def test_rest_assign_validation(make, tmp_path):
    h = await make()
    nid, project, conversation, roadmap = setup(h, TASKS + [{'id': 't4', 'title': 'Done', 'acceptance_criteria': ['x'], 'status': 'in_progress'}])
    assert (await assign(h, roadmap, 't4')).status_code == 409  # status rule
    assert (await assign(h, roadmap, 'missing')).status_code == 409
    assert (await h.http('post', 'agent/tasks/assign', json={'operation_id': op(), 'roadmap_id': 'f' * 32, 'task_id': 't1'})).status_code == 404
    assert (await h.http('post', 'agent/tasks/assign', json={'operation_id': op(), 'roadmap_id': roadmap['id'], 'task_id': 't1',
                                                              'role': 'coordinator'})).status_code == 422
    # A roadmap that is not the notebook's current roadmap, and a project-level roadmap.
    newer = h.application.roadmap_create(m.RoadmapCreate(operation_id=op(), project_id=project, notebook_id=nid, objective='Newer',
                                                         tasks=[m.Task(id='n1', title='New', acceptance_criteria=['x'])]))
    stale = await assign(h, roadmap, 't1')
    assert stale.status_code == 409 and "not this notebook's current roadmap" in stale.json()['detail']
    loose = h.application.roadmap_create(m.RoadmapCreate(operation_id=op(), project_id=project, objective='Project level',
                                                         tasks=[m.Task(id='p1', title='P', acceptance_criteria=['x'])]))
    assert (await assign(h, loose, 'p1')).status_code == 409
    # A notebook that left its project.
    other, other_conversation = h.standalone_notebook('Elsewhere')
    assert coding_runs(h) == []
    operation = op()
    ok = await assign(h, newer, 'n1', operation=operation)
    assert ok.status_code == 201
    assert (await assign(h, newer, 'n1', operation=operation)).json()['id'] == ok.json()['id']
    differs = await assign(h, newer, 'n1', operation=operation, instructions='something else')
    assert differs.status_code == 409
    h.app.state.agent_runtime = None
    h.app.state.agent_disabled_reason = 'No agent provider configured (set EUROPA_AGENT_PROVIDER)'
    assert (await assign(h, newer, 'n1')).status_code == 503


# ------------------------------------------------------------------ 3. Coordinator delegation is approval-gated

async def test_coordinator_delegation_requires_approval_denial_and_stale_revision_create_nothing(make):
    roles = Roles(coordinator=[call('delegate_task', task_id='t1', instructions='Build the matrix'), reply('Understood.'),
                               call('delegate_task', task_id='t1', instructions='Build the matrix'), reply('Roadmap changed.'),
                               call('delegate_task', task_id='t1', instructions='Build the matrix'), reply('Assigned.')],
                  coder=[reply('Nothing to run yet.')])
    h = await make(roles)
    nid, project, conversation, roadmap = setup(h)
    run = await h.ask(conversation, 'Assign the first task')
    assert (await h.settle(run['id']))['state'] == 'waiting_for_approval'
    approval = h.pending_approval(run['id'])
    assert approval['tool'] == 'delegate_task' and approval['preview']['title'] == "Assign 'Define a transition matrix' to the Coding Agent"
    assert approval['preview']['acceptance_criteria'] == ['Rows sum to 1'] and approval['preview']['instructions'] == 'Build the matrix'
    assert approval['revision'] == roadmap['revision'] and coding_runs(h) == []
    await h.approve(run['id'], 'deny')
    assert (await h.settle(run['id']))['state'] == 'completed' and coding_runs(h) == []
    # Stale: the owner edits the roadmap while the proposal waits.
    stale = await h.ask(conversation, 'Assign the first task')
    await h.settle(stale['id'])
    current = h.application.get(roadmap['id'], 'roadmap')
    h.application.roadmap_update(roadmap['id'], m.RoadmapUpdate(operation_id=op(), revision=current['revision'], objective='Edited',
                                                                  tasks=[m.Task(**t) for t in TASKS]))
    await h.approve(stale['id'])
    assert (await h.settle(stale['id']))['state'] == 'completed'
    (failed,) = h.runtime.store.tool_calls(stale['id'])
    assert (failed['state'], failed['result']['status']) == ('failed', 'conflict') and coding_runs(h) == []
    # Approved: a child run with the coordinator as parent.
    approved = await h.ask(conversation, 'Assign the first task')
    await h.settle(approved['id'])
    await h.approve(approved['id'])
    assert (await h.settle(approved['id'], states=('completed', 'failed')))['state'] == 'completed'
    (child,) = coding_runs(h)
    assert (child['parent_run_id'], child['depth'], child['trigger_message_id']) == (approved['id'], 1, approved['trigger_message_id'])
    assert child['task']['assigned_by'] == 'coordinator'
    await h.settle(child['id'])
    assert 'Execution record: nothing ran (no run of a cell was approved).' in h.messages(conversation)[-1]['data']['content']


# ------------------------------------------------------------------ 4. the Coding Agent cannot delegate, plan or create

async def test_coding_agent_cannot_delegate_plan_create_or_ask(make, monkeypatch):
    plan = {'objective': 'x', 'tasks': [{'key': 'a', 'title': 'A', 'acceptance_criteria': ['x']}]}
    h = await make(Roles(coder=[calls(('delegate_task', {'task_id': 't3', 'instructions': 'x'}), ('propose_roadmap', plan),
                                      ('create_project_notebook', {'notebook_name': 'x.ipynb', 'purpose': 'x'}),
                                      ('ask_owner', {'questions': ['x']})), reply('I cannot.')]))
    nid, project, conversation, roadmap = setup(h)
    run = (await assign(h, roadmap)).json()
    assert (await h.settle(run['id']))['state'] == 'completed'
    assert [(c['name'], c['state'], c['result']['status']) for c in h.runtime.store.tool_calls(run['id'])] == [
        ('delegate_task', 'rejected', 'policy'), ('propose_roadmap', 'rejected', 'policy'),
        ('create_project_notebook', 'rejected', 'policy'), ('ask_owner', 'rejected', 'policy')]
    sent = h.provider.calls[0]['tools']
    assert sorted(sent) == sorted(['read_notebook', 'read_execution', 'insert_code_cell', 'insert_markdown_cell',
                                   'replace_cell_source', 'execute_cell'])
    assert len(coding_runs(h)) == 1 and len(h.application.listing('roadmap', project=project)['items']) == 1


# ------------------------------------------------------------------ 5. owner edits win

async def test_owner_edit_between_proposal_and_approval_is_a_conflict_for_insert_and_replace(make):
    h = await make(Roles(coder=[insert(), lambda last: call('replace_cell_source', cell_id=owner_cell, source='owner_cell = 2', explanation='x'),
                                reply('Both edits conflicted.')]))
    nid, project, conversation, roadmap = setup(h)
    owner_cell = h.notebooks.open(nid)['cells'][0]['id']
    run = (await assign(h, roadmap)).json()
    await h.settle(run['id'])
    revision = h.notebooks.open(nid)['metadata']['europa']['revision']
    h.notebooks.edit_cell(nid, revision, 'insert', text='owner_added = 1')
    await h.approve(run['id'])
    await h.settle(run['id'])
    assert h.pending_approval(run['id'])['tool'] == 'replace_cell_source'
    revision = h.notebooks.open(nid)['metadata']['europa']['revision']
    h.notebooks.edit_cell(nid, revision, 'edit', owner_cell, text='owner_cell = "owner"')
    await h.approve(run['id'])
    assert (await h.settle(run['id']))['state'] == 'completed'
    assert [(c['name'], c['state'], c['result']['status']) for c in h.runtime.store.tool_calls(run['id'])] == [
        ('insert_code_cell', 'failed', 'conflict'), ('replace_cell_source', 'failed', 'conflict')]
    assert [c['source'] for c in h.notebooks.open(nid)['cells']] == ['owner_cell = "owner"', 'owner_added = 1']
    assert 'Execution record: nothing ran' in h.messages(conversation)[-1]['data']['content']


# ------------------------------------------------------------------ 6. only the agent's own cells run

async def test_execute_only_cells_this_run_wrote_with_unchanged_source(make):
    h = await make(Roles(coder=[lambda last: call('execute_cell', cell_id=owner_cell), insert(), execute_inserted,
                                lambda last: call('execute_cell', cell_id=inserted_id()), reply('Stopped.')]))
    nid, project, conversation, roadmap = setup(h)
    owner_cell = h.notebooks.open(nid)['cells'][0]['id']

    def inserted_id():
        return h.notebooks.open(nid)['cells'][1]['id']
    run = (await assign(h, roadmap)).json()
    await h.settle(run['id'])
    await h.approve(run['id'])  # insert
    await h.settle(run['id'])
    assert h.pending_approval(run['id'])['tool'] == 'execute_cell'
    # The owner edits the agent's cell: its source no longer matches what this run wrote.
    revision = h.notebooks.open(nid)['metadata']['europa']['revision']
    h.notebooks.edit_cell(nid, revision, 'edit', inserted_id(), text='print("owner")')
    await h.approve(run['id'], 'deny')
    assert (await h.settle(run['id']))['state'] == 'completed'
    results = [(c['name'], c['state'], c['result']['status'] if c['result'] else None) for c in h.runtime.store.tool_calls(run['id'])]
    assert results == [('execute_cell', 'rejected', 'policy'), ('insert_code_cell', 'succeeded', 'ok'),
                       ('execute_cell', 'denied', 'denied'), ('execute_cell', 'rejected', 'policy')]
    assert h.runtime.store.tool_calls(run['id'])[0]['result']['summary'] == NOT_OWN
    assert h.notebooks.executions(nid) == [] and h.service.begun == []
    assert 'Execution record: nothing ran (the run was denied).' in h.messages(conversation)[-1]['data']['content']


# ------------------------------------------------------------------ 7/8. deterministic execution ID, lost acknowledgement

def test_begin_with_execution_id_is_accepted_once(tmp_path):
    store = NotebookStore(tmp_path / 'nb')
    try:
        nid = store.create('Doc')['metadata']['europa']['notebook_id']
        document = store.edit_cell(nid, 1, 'insert', text='print(1)')
        cell, revision = document['cells'][0]['id'], document['metadata']['europa']['revision']
        execution = 'a' * 32
        receipt, code = store.begin(nid, cell, revision, execution)
        assert receipt['execution_id'] == execution and code == 'print(1)'
        current = store.open(nid)['metadata']['europa']['revision']
        with pytest.raises(NotebookError) as again:
            store.begin(nid, cell, current, execution)
        assert again.value.status_code == 409 and 'already accepted' in str(again.value)
        assert [r['execution_id'] for r in store.executions(nid)] == [execution]
        with pytest.raises(NotebookError):
            store.begin(nid, cell, current, 'not-hex')
        assert store.begin(nid, cell, current)[0]['execution_id'] != execution  # default stays random
    finally:
        store.close()


async def test_service_submit_passes_the_execution_id_and_refuses_a_replay(tmp_path):
    store = NotebookStore(tmp_path / 'nb')

    class NoRuntime:
        async def client_for(self, notebook):
            raise RuntimeError('no kernel in this test')
    service = NotebookService(store, NoRuntime())
    try:
        nid = store.create('Doc')['metadata']['europa']['notebook_id']
        document = store.edit_cell(nid, 1, 'insert', text='print(1)')
        cell, revision = document['cells'][0]['id'], document['metadata']['europa']['revision']
        receipt = await service.submit(nid, cell, revision, execution_id='b' * 32)
        assert receipt['execution_id'] == 'b' * 32
        await asyncio.gather(*tuple(service.tasks.values()), return_exceptions=True)
        assert store.execution(nid, 'b' * 32)['state'] == 'uncertain'  # failure after acceptance is never success
        with pytest.raises(NotebookError) as replay:
            await service.submit(nid, cell, store.open(nid)['metadata']['europa']['revision'], execution_id='b' * 32)
        assert replay.value.status_code == 409 and len(store.executions(nid)) == 1
    finally:
        await service.close(); store.close()


async def test_replayed_submit_and_lost_acknowledgement_bind_the_receipt_by_id(make):
    h = await make(Roles(coder=[insert(), execute_inserted, reply('Ran it.')]), modes=['lost'])
    nid, project, conversation, roadmap = setup(h)
    run = (await assign(h, roadmap)).json()
    assert await approve_all(h, run['id']) == 'completed'
    execute_call = next(c for c in h.runtime.store.tool_calls(run['id']) if c['name'] == 'execute_cell')
    assert execute_call['state'] == 'succeeded' and execute_call['result']['links']['execution_receipt_id'] == execute_call['id']
    assert h.service.begun == [execute_call['id']] and len(h.notebooks.executions(nid)) == 1
    assert 'row sum = 1.0' in h.messages(conversation)[-1]['data']['content']
    # Replaying the same call: begin refuses (already accepted) and the tool binds the existing receipt.
    from agents.tools import REGISTRY, ToolContext
    tool = REGISTRY['execute_cell']
    ctx = ToolContext(run=h.runtime.store.get_run(run['id']), services=h.runtime.services, call_id=execute_call['id'],
                      operation_id=execute_call['operation_id'])
    # The completed run's lease is gone, so bypass record_progress for this direct replay.
    original = h.runtime.record_progress

    async def no_progress(ctx, partial): return None
    h.runtime.record_progress = no_progress
    try:
        again = await tool.execute(ctx, tool.args.model_validate(execute_call['arguments']), execute_call['binding'])
    finally:
        h.runtime.record_progress = original
    assert again['links']['execution_receipt_id'] == execute_call['id']
    assert h.service.begun == [execute_call['id']] and len(h.notebooks.executions(nid)) == 1


async def test_refused_submission_reports_that_nothing_ran(make):
    h = await make(Roles(coder=[insert(), execute_inserted, reply('Could not run.')]), modes=['refuse'])
    nid, project, conversation, roadmap = setup(h)
    run = (await assign(h, roadmap)).json()
    assert await approve_all(h, run['id']) == 'completed'
    execute_call = next(c for c in h.runtime.store.tool_calls(run['id']) if c['name'] == 'execute_cell')
    assert (execute_call['state'], execute_call['result']['status']) == ('failed', 'conflict')
    assert h.notebooks.executions(nid) == []
    content = h.messages(conversation)[-1]['data']['content']
    assert 'Execution record: nothing ran (Execution was not accepted' in content and 'Observed output' not in content


# ------------------------------------------------------------------ 9. restart while executing

async def _crash_while_executing(h, nid, conversation, roadmap, receipt_state):
    """A dead control plane left an approved execute_cell 'executing'; its receipt is finished or running."""
    await h.stop()
    store = AgentStore(h.application)
    message = h.say(conversation, 'go')
    run, _ = store.create_run(operation_id=op(), payload={}, role='coding_agent', conversation_id=conversation,
                              trigger_message_id=message['id'], notebook_id=nid, project_id=roadmap['project_id'], parent_run_id=None,
                              depth=1, task={'roadmap_id': roadmap['id'], 'task_id': 't1', 'title': 'Define a transition matrix',
                                             'acceptance_criteria': ['Rows sum to 1'], 'instructions': 'x', 'assigned_by': 'user'},
                              provider='fake', model='fake-model', budget={'model_calls': 4, 'tool_calls': 8, 'steps': 24,
                              'active_seconds': 600.0, 'max_output_tokens': 2048, 'context_chars': 24000, 'repairs': 2})
    lease = ('dead', store.claim(run['id'], 'dead')['fence'])
    store.add_step(run['id'], lease, 'user', {'system': 'Role: Coding Agent', 'content': 'x', 'provenance': []})
    document = h.notebooks.open(nid)
    cell = document['cells'][0]
    store.add_step(run['id'], lease, 'assistant', {'content': '', 'tool_calls': [{'id': 'p1', 'name': 'execute_cell', 'arguments': '{}'}]})
    identity = op()
    store.add_tool_call(run['id'], lease, step_idx=1, provider_call_id='p1', name='execute_cell', version=1,
                        arguments={'cell_id': cell['id'], 'explanation': ''}, identity=identity, state='executing',
                        binding={'notebook_id': nid, 'revision': document['metadata']['europa']['revision'], 'cell_id': cell['id'],
                                 'source_hash': hashlib.sha256(cell['source'].encode()).hexdigest(), 'source': cell['source']})
    if receipt_state == 'none':
        return run, identity
    h.notebooks.begin(nid, cell['id'], document['metadata']['europa']['revision'], identity)
    if receipt_state == 'finished':
        h.notebooks.finish(nid, identity, {'status': 'ok', 'outputs': OK_OUTPUT, 'execution_count': 3})
    else:
        h.notebooks.update_execution(nid, identity, state='running')
    return run, identity


async def test_restart_binds_a_finished_receipt_without_rerunning(make):
    h = await make(Roles(coder=[reply('Recovered.')]))
    nid, project, conversation, roadmap = setup(h)
    run, identity = await _crash_while_executing(h, nid, conversation, roadmap, 'finished')
    await h.start()
    assert (await h.settle(run['id']))['state'] == 'completed'
    (execute_call,) = h.runtime.store.tool_calls(run['id'])
    assert execute_call['state'] == 'succeeded' and execute_call['result']['links']['execution_receipt_id'] == identity
    assert 'tool_reconciled' in h.events(run['id']) and h.service.begun == [] and len(h.notebooks.executions(nid)) == 1
    report = h.messages(conversation)[-1]['data']
    assert report['execution_receipt_id'] == identity and 'row sum = 1.0' in report['content']


async def test_restart_with_a_running_receipt_is_uncertain_and_interrupted(make):
    h = await make(Roles(coder=[reply('should not be asked')]))
    nid, project, conversation, roadmap = setup(h)
    run, identity = await _crash_while_executing(h, nid, conversation, roadmap, 'running')
    await h.start()
    assert (await h.settle(run['id']))['state'] == 'interrupted'
    (execute_call,) = h.runtime.store.tool_calls(run['id'])
    assert execute_call['state'] == 'uncertain' and h.service.begun == [] and len(h.notebooks.executions(nid)) == 1
    assert h.provider.calls == [] and task(h, roadmap, 't1')['status'] == 'pending'


async def test_restart_with_no_receipt_runs_once_with_the_same_id(make):
    h = await make(Roles(coder=[reply('Ran after restart.')]))
    nid, project, conversation, roadmap = setup(h)
    run, identity = await _crash_while_executing(h, nid, conversation, roadmap, 'none')
    await h.start()
    assert (await h.settle(run['id']))['state'] == 'completed'
    (execute_call,) = h.runtime.store.tool_calls(run['id'])
    assert execute_call['state'] == 'succeeded' and h.service.begun == [identity]
    assert [r['execution_id'] for r in h.notebooks.executions(nid)] == [identity]
    reconciled = next(e for e in h.runtime.store.events(run['id'])['items'] if e['type'] == 'tool_reconciled')
    assert reconciled['data']['outcome'] == 'not_applied'


# ------------------------------------------------------------------ 10. repair rule and budget

async def test_repair_rule_and_repair_budget(make):
    def replace(source):
        return lambda last: call('replace_cell_source', cell_id=cell_holder['id'], source=source, explanation='repair')

    def run_cell(last):
        return call('execute_cell', cell_id=cell_holder['id'])

    def remember(last):
        cell_holder['id'] = last['data']['cell_id']
        return call('execute_cell', cell_id=last['data']['cell_id'])
    cell_holder = {}
    h = await make(Roles(coder=[insert('1 / 0'), remember,
                                lambda last: calls(('insert_code_cell', {'source': 'print("unrelated")', 'explanation': 'x'}),  # not a repair → policy
                                                   ('replace_cell_source', {'cell_id': owner_cell, 'source': 'x = 1', 'explanation': 'x'})),  # other cell → policy
                                replace('1 / 0  # repair 1'), lambda last: call('insert_code_cell', source='y = 2', explanation='x'),  # must run first → policy
                                run_cell, replace('1 / 0  # repair 2'), run_cell,
                                replace('1 / 0  # repair 3'),                       # budget exhausted → policy
                                reply('Stopped after two repairs.')]),
                   modes=['error', 'error', 'error'])
    nid, project, conversation, roadmap = setup(h)
    owner_cell = h.notebooks.open(nid)['cells'][0]['id']
    run = (await assign(h, roadmap)).json()
    assert await approve_all(h, run['id']) == 'completed'
    states = [(c['name'], c['state'], (c['result'] or {}).get('status')) for c in h.runtime.store.tool_calls(run['id'])]
    assert states == [('insert_code_cell', 'succeeded', 'ok'), ('execute_cell', 'succeeded', 'error'),
                      ('insert_code_cell', 'rejected', 'policy'), ('replace_cell_source', 'rejected', 'policy'),
                      ('replace_cell_source', 'succeeded', 'ok'), ('insert_code_cell', 'rejected', 'policy'),
                      ('execute_cell', 'succeeded', 'error'), ('replace_cell_source', 'succeeded', 'ok'),
                      ('execute_cell', 'succeeded', 'error'), ('replace_cell_source', 'rejected', 'policy')]
    rejected = [c['result']['summary'] for c in h.runtime.store.tool_calls(run['id']) if c['state'] == 'rejected']
    assert 'replace_cell_source on that cell' in rejected[0] and 'replace_cell_source on that cell' in rejected[1]
    assert 'execute_cell on the repaired cell' in rejected[2] and 'Repair budget exhausted' in rejected[3]
    assert len(h.service.begun) == 3
    content = h.messages(conversation)[-1]['data']['content']
    assert 'applied, status error' in content and 'ZeroDivisionError: division by zero' in content


async def test_markdown_note_is_allowed_after_an_error(make):
    h = await make(Roles(coder=[insert('1 / 0'), execute_inserted,
                                call('insert_markdown_cell', source='The division failed.', explanation='note'), reply('Noted.')]),
                   modes=['error'])
    nid, project, conversation, roadmap = setup(h)
    run = (await assign(h, roadmap)).json()
    assert await approve_all(h, run['id']) == 'completed'
    assert [c['state'] for c in h.runtime.store.tool_calls(run['id'])] == ['succeeded', 'succeeded', 'succeeded']


# ------------------------------------------------------------------ 11. cancel keeps committed work

async def test_cancel_after_committed_insert_keeps_the_cell(make):
    h = await make(Roles(coder=[insert(), execute_inserted]))
    nid, project, conversation, roadmap = setup(h)
    run = (await assign(h, roadmap)).json()
    await h.settle(run['id'])
    await h.approve(run['id'])
    await h.settle(run['id'])
    assert h.pending_approval(run['id'])['tool'] == 'execute_cell'
    cancelled = await h.http('post', f"agent/runs/{run['id']}/cancel", json={'operation_id': op()})
    assert cancelled.json()['state'] == 'cancelled'
    assert [c['source'] for c in h.notebooks.open(nid)['cells']] == ['owner_cell = 1', MATRIX_CODE]
    event = next(e for e in h.runtime.store.events(run['id'])['items'] if e['type'] == 'run_cancelled')
    assert 'not undone' in event['data']['note']
    assert h.notebooks.executions(nid) == [] and coding_runs(h)[0]['state'] == 'cancelled'
    assert (await assign(h, roadmap, 't3')).status_code == 201  # a cancelled run no longer blocks assignment


# ------------------------------------------------------------------ demo provider

async def test_demo_coding_agent_inserts_runs_and_reports(make):
    h = await make(provider=DemoProvider())
    nid, project, conversation, roadmap = setup(h)
    run = (await assign(h, roadmap)).json()
    await h.settle(run['id'])
    assert h.pending_approval(run['id'])['preview']['source'] == MATRIX_CODE
    assert await approve_all(h, run['id']) == 'completed'
    content = h.messages(conversation)[-1]['data']['content']
    assert content.startswith('I added the cell and ran it') and 'row sum = 1.0' in content


async def test_demo_coding_agent_repairs_once_then_reports(make):
    h = await make(provider=DemoProvider(), modes=['error', 'error'])
    nid, project, conversation, roadmap = setup(h)
    run = (await assign(h, roadmap)).json()
    assert await approve_all(h, run['id']) == 'completed'
    assert [c['name'] for c in h.runtime.store.tool_calls(run['id'])] == ['insert_code_cell', 'execute_cell', 'replace_cell_source', 'execute_cell']
    assert 'stopped after one repair' in h.messages(conversation)[-1]['data']['content']


async def test_demo_coding_agent_reports_a_denial_honestly(make):
    h = await make(provider=DemoProvider())
    nid, project, conversation, roadmap = setup(h)
    run = (await assign(h, roadmap)).json()
    await h.settle(run['id'])
    await h.approve(run['id'], 'deny')
    assert (await h.settle(run['id']))['state'] == 'completed'
    content = h.messages(conversation)[-1]['data']['content']
    assert 'nothing ran' in content and 'Observed output' not in content


def test_demo_code_is_task_specific_and_never_embeds_the_title():
    assert 'row sum = ' in task_code('Define a transition matrix') and 'random.seed(7)' in task_code('Simulate transitions')
    assert 'for _ in range(100)' in task_code('Estimate the stationary distribution') and 'gap = ' in task_code('Visualize convergence')
    assert task_code('"); import os; os.system("x') == task_code('Anything')
    for title in ('Define a transition matrix', 'Simulate transitions', 'Estimate the stationary distribution', 'Visualize convergence', 'x'):
        compile(task_code(title), 'demo', 'exec')
