"""Phase 4D Stage 1: kickoff questionnaire — a Home creation starts one Notebook Coordinator
kickoff run (questions, plus a draft roadmap in a project), exactly once across restarts."""
import json
import re

import pytest
import pytest_asyncio

from agents.demo import DemoProvider, kickoff_questions, subject
from agents.providers import FakeProvider
from agents.store import AgentStore
from agents.tools import REGISTRY, ToolContext
from agent_support import Harness, call, op, reply

PROJECT, NOTEBOOK = 'Revenue Forecasting', 'exploratory-analysis.ipynb'
MARKOV = 'I want to understand a simple Markov chain.'
NUMBERED = re.compile(r'^(\d+)\. (.+)$', re.MULTILINE)


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


def create(project=PROJECT, notebook=NOTEBOOK, purpose='Forecast revenue from sales data'):
    arguments = {'notebook_name': notebook, 'purpose': purpose}
    if project is not None: arguments['project_name'] = project
    return call('create_project_notebook', note='Proposing a notebook.', **arguments)


def home(h):
    return h.application.ensure_workspace_conversation()['id']


def kickoffs(h):
    with h.application.bookkeeping() as db:
        rows = db.execute("SELECT id FROM agent_runs WHERE role='notebook_coordinator' AND operation_id LIKE 'agent-kickoff-%' "
                          'ORDER BY seq').fetchall()
    return [h.runtime.store.get_run(r['id']) for r in rows]


def notebook_runs(h):
    with h.application.bookkeeping() as db:
        return db.execute("SELECT count(*) FROM agent_runs WHERE role='notebook_coordinator'").fetchone()[0]


async def created_notebook(h, script_home_run=True):
    """Ask Home to create, approve, settle the Home run; return the create call's result data."""
    run = await h.ask(home(h), 'I have sales data and want to forecast revenue.' if script_home_run else MARKOV)
    await h.settle(run['id'])
    await h.approve(run['id'])
    assert (await h.settle(run['id']))['state'] == 'completed'
    (creation,) = [c for c in h.runtime.store.tool_calls(run['id']) if c['name'] == 'create_project_notebook']
    assert creation['state'] == 'succeeded'
    return run, creation


def questions_of(message):
    return [text for _, text in NUMBERED.findall(message['data']['content'])]


# ------------------------------------------------------------------ start and scope

async def test_home_create_starts_exactly_one_kickoff_run_in_the_new_notebook(make):
    kickoff = FakeProvider([call('ask_owner', questions=['What is the revenue target?', 'Which sales data?', 'What outputs?']),
                            reply('Please answer the questions above.')])
    h = await make([create(), reply('Created.')], kickoff=kickoff)
    run, creation = await created_notebook(h)
    await h.runtime.idle()
    data = creation['result']['data']
    (started,) = kickoffs(h)
    assert notebook_runs(h) == 1
    assert started['operation_id'] == 'agent-kickoff-' + creation['id']
    assert (started['role'], started['conversation_id'], started['notebook_id'], started['project_id']) == (
        'notebook_coordinator', data['conversation_id'], data['notebook_id'], data['project_id'])
    assert started['trigger_message_id'] == data['origin_message_id'] and started['parent_run_id'] is None and started['depth'] == 0
    assert started['task'] == {'kickoff': True, 'purpose': 'Forecast revenue from sales data'}
    assert started['state'] == 'completed'
    system, user = kickoff.calls[0]['messages'][0]['content'], kickoff.calls[0]['messages'][1]['content']
    assert 'Kickoff run:' in system and 'ask_owner' in system
    assert user.rstrip().endswith("Owner message:\nKickoff for a new notebook. Owner's purpose: Forecast revenue from sales data")
    assert 'assigned-task' not in user  # the kickoff task is not fenced as a Coding Agent assignment
    assert 'ask_owner' in kickoff.calls[0]['tools'] and 'propose_roadmap' in kickoff.calls[0]['tools']
    messages = h.messages(data['conversation_id'])
    assert [(x['data']['author_role'], x['data']['state']) for x in messages] == [
        ('coordinator', 'published'), ('coordinator', 'needs_input'), ('coordinator', 'published')]
    assert messages[1]['data']['content'] == '1. What is the revenue target?\n2. Which sales data?\n3. What outputs?'
    (asked,) = h.runtime.store.tool_calls(started['id'])
    assert asked['name'] == 'ask_owner' and asked['state'] == 'succeeded' and asked['result']['data']['message_id'] == messages[1]['id']
    # The question message is idempotent by the call's own operation ID.
    assert asked['operation_id'] == 'agent-' + asked['id']
    # The Home run is unaffected: the kickoff is a separate run, and no approval covered it.
    assert 'kickoff_failed' not in h.events(run['id'])
    # The kickoff's own model input shows the question message as needs_input in a later run's history.
    later = FakeProvider([reply('Thanks.')])
    h.kickoff = later
    await h.restart(FakeProvider([reply('Noted.')]))
    answer = await h.ask(data['conversation_id'], 'The target is 5% growth.')
    assert (await h.settle(answer['id']))['state'] == 'completed'
    history = h.provider.calls[0]['messages'][1]['content']
    assert '"needs_input": true' in history and history.count('"needs_input"') == 1


async def test_rest_cannot_start_a_kickoff_run(make):
    h = await make([create(), reply('Created.')])
    _, creation = await created_notebook(h)
    data = creation['result']['data']
    response = await h.http('post', 'agent/runs', json={'operation_id': op(), 'conversation_id': data['conversation_id'],
                                                         'message_id': data['origin_message_id']})
    assert response.status_code == 422  # coordinator-authored trigger
    for body in ({'operation_id': op(), 'notebook_id': data['notebook_id']},
                 {'operation_id': op(), 'conversation_id': data['conversation_id'], 'message_id': data['origin_message_id'],
                  'task': {'kickoff': True, 'purpose': 'x'}}):
        assert (await h.http('post', 'agent/runs', json=body)).status_code == 422
    await h.runtime.idle()
    assert len(kickoffs(h)) == 1
    # The UI polls the notebook conversation's runs: the kickoff run is listed, its purpose is not exposed.
    listed = await h.http('get', f"agent/runs?conversation_id={data['conversation_id']}")
    assert listed.status_code == 200
    (view,) = listed.json()['items']
    assert view['task'] == {'kickoff': True} and view['trigger_message_id'] == data['origin_message_id']


async def test_denied_create_starts_no_kickoff(make):
    h = await make([create(), reply('Nothing was created.')])
    run = await h.ask(home(h), 'I have sales data and want to forecast revenue.')
    await h.settle(run['id'])
    await h.approve(run['id'], 'deny')
    assert (await h.settle(run['id']))['state'] == 'completed'
    await h.runtime.idle()
    assert notebook_runs(h) == 0 and h.notebooks.list() == []
    await h.restart([])
    await h.runtime.idle()
    assert notebook_runs(h) == 0


async def test_kickoff_start_failure_does_not_fail_the_home_run(make, monkeypatch):
    h = await make([create(), reply('Created.')])

    async def broken(*args, **kwargs):
        raise RuntimeError('kickoff storage unavailable')
    monkeypatch.setattr(h.runtime, 'start_kickoff', broken)
    run, creation = await created_notebook(h)
    assert 'kickoff_failed' in h.events(run['id']) and notebook_runs(h) == 0
    event = next(e for e in h.runtime.store.events(run['id'])['items'] if e['type'] == 'kickoff_failed')
    assert event['data']['notebook_id'] == creation['result']['data']['notebook_id']


# ------------------------------------------------------------------ restart exactly-once

async def _crash_after_succeeded(h, run_id, *, drop_origin=False):
    """Approve while stopped and apply the create, then record the call 'succeeded' as the
    harness does, but die before the kickoff and before the tool step: the run stays running."""
    await h.stop()
    store = AgentStore(h.application)
    approval = next(a for a in store.approvals(run_id) if a['state'] == 'pending')
    store.decide(approval['id'], operation_id=op(), decision='approve', digest_value=approval['digest'])
    (tool_call,) = store.tool_calls(run_id)
    services = type('S', (), {'app': h.application, 'notebooks': h.notebooks, 'workspace': h.app.state.workspace,
                              'mutation': h.mutation})()
    ctx = ToolContext(run=store.get_run(run_id), services=services, call_id=tool_call['id'], operation_id=tool_call['operation_id'])
    tool = REGISTRY['create_project_notebook']
    result = await tool.execute(ctx, tool.args.model_validate(tool_call['arguments']), tool_call['binding'])
    if drop_origin:  # what a pre-4D server recorded
        result['data'].pop('origin_message_id')
    with h.application.bookkeeping() as db:
        db.execute("UPDATE agent_runs SET state='running', owner='dead' WHERE id=?", (run_id,))
    with h.application.bookkeeping() as db:
        db.execute("UPDATE agent_tool_calls SET state='succeeded', result=? WHERE id=?",
                   (json.dumps(result), tool_call['id']))
    assert [s['kind'] for s in store.steps(run_id)] == ['user', 'assistant'] and notebook_runs(h) == 0
    return tool_call, result


async def test_restart_between_create_and_kickoff_starts_it_exactly_once(make):
    h = await make([create()])
    run = await h.ask(home(h), 'I have sales data and want to forecast revenue.')
    await h.settle(run['id'])
    tool_call, result = await _crash_after_succeeded(h, run['id'])
    await h.restart([reply('Created after restart.')])
    assert (await h.settle(run['id']))['state'] == 'completed'
    await h.runtime.idle()
    (started,) = kickoffs(h)
    assert started['operation_id'] == 'agent-kickoff-' + tool_call['id']
    assert started['trigger_message_id'] == result['data']['origin_message_id'] and started['state'] == 'completed'
    await h.restart([])
    await h.runtime.idle()
    assert [r['id'] for r in kickoffs(h)] == [started['id']] and notebook_runs(h) == 1
    await h.restart([])
    await h.runtime.idle()
    assert notebook_runs(h) == 1


async def test_restart_never_kicks_off_a_creation_recorded_before_phase_4d(make):
    h = await make([create()])
    run = await h.ask(home(h), 'I have sales data and want to forecast revenue.')
    await h.settle(run['id'])
    await _crash_after_succeeded(h, run['id'], drop_origin=True)
    await h.restart([reply('Created after restart.')])
    assert (await h.settle(run['id']))['state'] == 'completed'
    await h.runtime.idle()
    assert notebook_runs(h) == 0


# ------------------------------------------------------------------ ask_owner

async def test_ask_owner_once_per_run_and_replay_posts_no_duplicate(make):
    first = {'questions': ['Which data?', 'Which output?'], 'preamble': 'Two questions:'}
    h = await make([call('ask_owner', **first), call('ask_owner', questions=['Again?']), call('ask_owner', questions=['x'] * 7),
                    reply('Waiting for your answers.')])
    nid, project, conversation = h.project_notebook()
    run = await h.ask(conversation, 'Help me start.')
    assert (await h.settle(run['id']))['state'] == 'completed'
    calls = h.runtime.store.tool_calls(run['id'])
    assert [(c['name'], c['state'], c['result']['status']) for c in calls] == [
        ('ask_owner', 'succeeded', 'ok'), ('ask_owner', 'rejected', 'policy'), ('ask_owner', 'rejected', 'policy')]
    assert 'at most 1 time(s) per run' in calls[1]['result']['summary']
    messages = h.messages(conversation)
    assert [(x['data']['author_role'], x['data']['state']) for x in messages] == [
        ('user', 'published'), ('coordinator', 'needs_input'), ('coordinator', 'published')]
    assert messages[1]['data']['content'] == 'Two questions:\n\n1. Which data?\n2. Which output?'
    # Replaying the committed operation (as recovery does) returns the same message.
    tool = REGISTRY['ask_owner']
    ctx = ToolContext(run=h.runtime.store.get_run(run['id']), services=h.runtime.services, call_id=calls[0]['id'],
                      operation_id=calls[0]['operation_id'])
    replay = await tool.execute(ctx, tool.args.model_validate(calls[0]['arguments']), None)
    assert replay['data']['message_id'] == messages[1]['id'] and len(h.messages(conversation)) == 3
    # No approval was requested for asking.
    assert h.runtime.store.approvals(run['id']) == []


async def test_ask_owner_argument_bounds(make):
    h = await make([call('ask_owner', questions=[]), call('ask_owner', questions=['q' * 501]),
                    call('ask_owner', questions=['ok'], preamble='p' * 1001), call('ask_owner', questions=['  ']),
                    reply('Done.')])
    nid, project, conversation = h.project_notebook()
    run = await h.ask(conversation, 'Help me start.')
    assert (await h.settle(run['id']))['state'] == 'completed'
    assert [c['result']['status'] for c in h.runtime.store.tool_calls(run['id'])] == ['invalid'] * 4
    assert len(h.messages(conversation)) == 2


async def test_home_coordinator_can_ask_the_owner(make):
    h = await make([call('ask_owner', questions=['What should the notebook answer?']), reply('Tell me more.')])
    conversation = home(h)
    run = await h.ask(conversation, 'Something about data.')
    assert (await h.settle(run['id']))['state'] == 'completed'
    assert [x['data']['state'] for x in h.messages(conversation)] == ['published', 'needs_input', 'published']


# ------------------------------------------------------------------ demo provider journeys

def test_demo_kickoff_questions_name_the_subject():
    assert subject('Owner objective: I want to understand a simple Markov chain.') == 'Markov chain'
    questions = kickoff_questions('Owner objective: I want to understand a simple Markov chain.')
    assert 3 <= len(questions) <= 5 and all('Markov chain' in q for q in questions)


async def test_demo_kickoff_in_project_asks_questions_and_proposes_a_draft_then_revises_from_an_answer(make):
    h = await make(DemoProvider())
    run, creation = await created_notebook(h, script_home_run=False)
    data = creation['result']['data']
    (started,) = kickoffs(h)
    waiting = await h.settle(started['id'])
    assert waiting['state'] == 'waiting_for_approval'
    messages = h.messages(data['conversation_id'])
    assert [(x['data']['author_role'], x['data']['state']) for x in messages] == [
        ('coordinator', 'published'), ('coordinator', 'needs_input')]
    questions = questions_of(messages[1])
    assert 3 <= len(questions) <= 5 and all('Markov chain' in q for q in questions)
    assert [int(n) for n, _ in NUMBERED.findall(messages[1]['data']['content'])] == list(range(1, len(questions) + 1))
    draft = h.pending_approval(started['id'])
    assert draft['tool'] == 'propose_roadmap' and draft['preview']['title'] == 'Create roadmap'
    assert [t['title'] for t in draft['preview']['tasks']] == [
        'Define a transition matrix', 'Simulate transitions', 'Estimate the stationary distribution', 'Visualize convergence']
    note = next(e for e in h.runtime.store.events(started['id'])['items'] if e['type'] == 'agent_note' and 'draft' in e['data']['text'])
    assert 'answers can refine it' in note['data']['text'] and 'nothing is saved until you approve' in note['data']['text']
    assert h.application.listing('roadmap', project=data['project_id'])['items'] == []  # nothing saved before approval
    # Approve the draft before answering.
    await h.approve(started['id'])
    assert (await h.settle(started['id']))['state'] == 'completed'
    (roadmap,) = h.application.listing('roadmap', project=data['project_id'])['items']
    assert h.messages(data['conversation_id'])[-1]['data']['content'].startswith('Saved the draft roadmap')
    # The owner answers a question through the normal composer path: a normal Coordinator run.
    answer = await h.ask(data['conversation_id'], 'I will simulate a three-state weather chain.')
    assert answer['role'] == 'notebook_coordinator' and answer['task'] is None
    assert (await h.settle(answer['id']))['state'] == 'waiting_for_approval'
    revision = h.pending_approval(answer['id'])
    assert revision['preview']['title'] == 'Revise roadmap'
    assert revision['revision'] == revision['preview']['roadmap_revision'] == roadmap['revision']
    titles = [t['title'] for t in revision['preview']['tasks']]
    assert titles[:4] == [t['title'] for t in roadmap['data']['tasks']]
    assert titles[4:] == ['Use your answer: I will simulate a three-state weather chain.']
    await h.approve(answer['id'])
    assert (await h.settle(answer['id']))['state'] == 'completed'
    (revised,) = h.application.listing('roadmap', project=data['project_id'])['items']
    assert revised['id'] == roadmap['id'] and revised['revision'] == roadmap['revision'] + 1 and len(revised['data']['tasks']) == 5
    # Answered: a later status request is not treated as another answer.
    status = await h.ask(data['conversation_id'], 'What is the status?')
    assert (await h.settle(status['id']))['state'] == 'completed'
    assert h.runtime.store.approvals(status['id']) == []


async def test_demo_answer_without_a_saved_draft_proposes_a_fresh_draft(make):
    h = await make(DemoProvider())
    _, creation = await created_notebook(h, script_home_run=False)
    data = creation['result']['data']
    (started,) = kickoffs(h)
    await h.settle(started['id'])
    await h.approve(started['id'], 'deny')
    assert (await h.settle(started['id']))['state'] == 'completed'
    answer = await h.ask(data['conversation_id'], 'Use a two-state chain.')
    assert (await h.settle(answer['id']))['state'] == 'waiting_for_approval'
    proposal = h.pending_approval(answer['id'])
    assert proposal['preview']['title'] == 'Create roadmap' and proposal['preview']['roadmap_revision'] is None
    assert proposal['preview']['tasks'][-1]['title'] == 'Use your answer: Use a two-state chain.'


async def test_demo_standalone_kickoff_asks_questions_only(make):
    h = await make([create(project=None, notebook='markov-chain.ipynb', purpose=f'Owner objective: {MARKOV}'), reply('Created.')],
                   kickoff=DemoProvider())
    _, creation = await created_notebook(h)
    data = creation['result']['data']
    assert data['project_id'] is None
    (started,) = kickoffs(h)
    assert (await h.settle(started['id']))['state'] == 'completed' and started['project_id'] is None
    assert h.runtime.store.approvals(started['id']) == []
    assert [c['name'] for c in h.runtime.store.tool_calls(started['id'])] == ['ask_owner']
    messages = h.messages(data['conversation_id'])
    assert [x['data']['state'] for x in messages] == ['published', 'needs_input', 'published']
    assert 3 <= len(questions_of(messages[1])) <= 5
    assert 'Planning needs a project' in messages[2]['data']['content']
