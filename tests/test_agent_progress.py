"""Phase 4D Stage 3: durable roadmap progress for Coding Agent runs and verified auto-completion."""
import asyncio

from agents.providers import ProviderError
from agents.store import AgentStore
from application import models as m
from agent_support import call, op, reply
from test_agent_coding import (MATRIX_CODE, TASKS, Roles, approve_all, assign, execute_inserted, insert, make,  # noqa: F401 (fixture)
                               setup, task)


def activities(h, project):
    return [a for a in h.application.listing('activity', project=project, limit=100)['items'] if a['data']['event_type'] == 'task_completed']


def events(h, run_id, kind):
    return [e for e in h.runtime.store.events(run_id, 0, 200)['items'] if e['type'] == kind]


def progress(h, roadmap):
    return h.application.get(roadmap['id'], 'roadmap')['data']['progress']


async def started(h, roadmap, task_id='t1'):
    response = await assign(h, roadmap, task_id)
    assert response.status_code == 201, response.text
    return response.json()


# ------------------------------------------------------------------ start, waiting, verified success

async def test_start_clears_explanation_waiting_is_unchanged_and_verified_success_completes(make):
    h = await make(Roles(coder=[insert(), execute_inserted, reply('Rows sum to 1.')]))
    blocked_first = [{**TASKS[0], 'status': 'blocked', 'explanation': 'Waiting for data'}] + TASKS[1:]
    nid, project, conversation, roadmap = setup(h, blocked_first)
    run = await started(h, roadmap)
    assert (task(h, roadmap, 't1')['status'], task(h, roadmap, 't1')['explanation']) == ('in_progress', None)
    assert [e['data']['operation_id'] for e in events(h, run['id'], 'task_progress')] == ['agent-start-' + run['id']]
    assert (await h.settle(run['id']))['state'] == 'waiting_for_approval'
    assert task(h, roadmap, 't1')['status'] == 'in_progress' and progress(h, roadmap) == 0
    assert await approve_all(h, run['id']) == 'completed'
    done = task(h, roadmap, 't1')
    execute = next(c for c in h.runtime.store.tool_calls(run['id']) if c['name'] == 'execute_cell')
    report = h.messages(conversation)[-1]
    assert (done['status'], done['accepted'], done['accepted_by'], done['explanation']) == ('completed', True, 'coding_agent', None)
    assert done['links'] == {**done['links'], 'notebook_id': nid, 'cell_id': execute['arguments']['cell_id']}
    (evidence,) = done['evidence']
    assert (evidence['execution_receipt_id'], evidence['message_id'], evidence['cell_ids']) == (execute['id'], report['id'], [])
    assert evidence['summary'] == f"verified by receipt {execute['id'][:8]} (applied, status ok)"
    assert progress(h, roadmap) == 33.33 and done['display']['checkmark'] is True
    (activity,) = activities(h, project)
    assert (activity['data']['actor'], activity['data']['result'], activity['data']['execution_receipt_id'],
            activity['data']['cell_id'], activity['data']['task_id']) == ('coding_agent', 'succeeded', execute['id'], done['links']['cell_id'], 't1')
    # Completion unblocks the next task's assignment.
    assert (await assign(h, roadmap, 't2')).status_code == 201
    # Operation-ID replay adds no evidence and no activity.
    record = h.runtime.store.get_run(run['id'])
    await asyncio.to_thread(h.runtime.progress.finished, record, report, 'again')
    assert len(task(h, roadmap, 't1')['evidence']) == 1 and len(activities(h, project)) == 1
    # The coding_agent attribution survives an owner edit of another task.
    current = h.application.get(roadmap['id'], 'roadmap')
    third = next(t for t in current['data']['tasks'] if t['id'] == 't3')
    h.application.roadmap_update(roadmap['id'], m.TaskUpdate(operation_id=op(), revision=current['revision'],
                                 task=m.Task.model_validate({**{k: third[k] for k in m.Task.model_fields}, 'explanation': 'owner note'})), True)
    assert task(h, roadmap, 't1')['accepted_by'] == 'coding_agent'


async def test_notes_only_task_completes_with_cell_evidence(make):
    h = await make(Roles(coder=[call('insert_markdown_cell', source='## Notes\nRows sum to one.', explanation='notes'), reply('Wrote notes.')]))
    nid, project, conversation, roadmap = setup(h)
    run = await started(h, roadmap)
    assert await approve_all(h, run['id']) == 'completed'
    done = task(h, roadmap, 't1')
    note = h.notebooks.open(nid)['cells'][-1]
    assert (done['status'], done['accepted_by'], done['links']['cell_id']) == ('completed', 'coding_agent', note['id'])
    (evidence,) = done['evidence']
    assert (evidence['execution_receipt_id'], evidence['cell_ids']) == (None, [note['id']])
    assert evidence['message_id'] == h.messages(conversation)[-1]['id']
    assert activities(h, project)[0]['data']['execution_receipt_id'] is None


# ------------------------------------------------------------------ no completion without verified success

async def test_code_edit_without_execution_does_not_complete(make):
    h = await make(Roles(coder=[insert(), reply('I wrote it but did not run it.')]))
    nid, project, conversation, roadmap = setup(h)
    run = await started(h, roadmap)
    assert await approve_all(h, run['id']) == 'completed'
    current = task(h, roadmap, 't1')
    assert current['status'] == 'in_progress' and not current['accepted']
    assert current['explanation'].startswith('Coding Agent finished without verified success: cell ') and 'never ran successfully' in current['explanation']
    assert len(current['evidence']) == 1 and current['evidence'][0]['execution_receipt_id'] is None
    assert activities(h, project) == [] and progress(h, roadmap) == 0


async def test_error_receipt_does_not_complete(make):
    h = await make(Roles(coder=[insert('1 / 0'), execute_inserted, reply('It failed.')]), modes=['error'])
    nid, project, conversation, roadmap = setup(h)
    run = await started(h, roadmap)
    assert await approve_all(h, run['id']) == 'completed'
    current = task(h, roadmap, 't1')
    assert current['status'] == 'in_progress' and 'did not succeed' in current['explanation'] and activities(h, project) == []


async def test_edit_after_the_last_run_does_not_complete(make):
    h = await make(Roles(coder=[insert(), execute_inserted,
                                lambda last: call('replace_cell_source', cell_id=last['data']['cell_id'], source='P = 1', explanation='x'),
                                reply('Changed it afterwards.')]))
    nid, project, conversation, roadmap = setup(h)
    run = await started(h, roadmap)
    assert await approve_all(h, run['id']) == 'completed'
    current = task(h, roadmap, 't1')
    assert current['status'] == 'in_progress' and 'edited after its last run' in current['explanation']


# ------------------------------------------------------------------ failure, cancellation, denial, interruption

async def test_failed_run_blocks_the_task_with_its_reason(make):
    def boom(last):
        raise ProviderError('Model provider returned HTTP 503')
    h = await make(Roles(coder=[boom]))
    nid, project, conversation, roadmap = setup(h)
    run = await started(h, roadmap)
    assert (await h.settle(run['id']))['state'] == 'failed'
    current = task(h, roadmap, 't1')
    assert (current['status'], current['explanation']) == ('blocked', 'Coding Agent: run failed: Model provider returned HTTP 503')


async def test_budget_exhaustion_blocks_the_task(make):
    h = await make(Roles(coder=[call('read_notebook')] * 12))
    nid, project, conversation, roadmap = setup(h)
    run = await started(h, roadmap)
    assert (await h.settle(run['id']))['state'] == 'failed'
    current = task(h, roadmap, 't1')
    assert current['status'] == 'blocked' and current['explanation'] == 'Coding Agent: run failed: Budget exhausted: model requests'


async def test_cancel_without_evidence_returns_the_task_to_pending(make):
    h = await make(Roles(coder=[insert()]))
    nid, project, conversation, roadmap = setup(h)
    run = await started(h, roadmap)
    await h.settle(run['id'])
    assert (await h.http('post', f"agent/runs/{run['id']}/cancel", json={'operation_id': op()})).json()['state'] == 'cancelled'
    current = task(h, roadmap, 't1')
    assert (current['status'], current['explanation'], current['evidence']) == ('pending', None, [])
    assert events(h, run['id'], 'task_progress')[-1]['data']['operation_id'] == 'agent-reset-' + run['id']


async def test_cancel_of_a_running_run_returns_the_task_to_pending(make):
    gate = asyncio.Event()

    async def slow(last):
        await gate.wait()
        return insert()
    h = await make(Roles(coder=[slow]))
    nid, project, conversation, roadmap = setup(h)
    run = await started(h, roadmap)
    await h.settle(run['id'], states=('running',))
    assert (await h.runtime.cancel(run['id']))['state'] == 'cancelling'
    gate.set()
    assert (await h.settle(run['id']))['state'] == 'cancelled'
    assert task(h, roadmap, 't1')['status'] == 'pending'


async def test_every_proposal_denied_returns_the_task_to_pending(make):
    h = await make(Roles(coder=[insert(), reply('Understood, nothing was changed.')]))
    nid, project, conversation, roadmap = setup(h)
    run = await started(h, roadmap)
    await h.settle(run['id'])
    await h.approve(run['id'], 'deny')
    assert (await h.settle(run['id']))['state'] == 'completed'
    current = task(h, roadmap, 't1')
    assert (current['status'], current['evidence']) == ('pending', [])


async def test_interrupted_run_blocks_the_task(make):
    h = await make(Roles(coder=[insert(), execute_inserted]), modes=['running'])
    nid, project, conversation, roadmap = setup(h)
    run = await started(h, roadmap)
    await h.settle(run['id']); await h.approve(run['id'])       # insert
    await h.settle(run['id']); await h.approve(run['id'])       # execute: the receipt stays running
    await h.settle(run['id'], states=('running',))
    deadline = asyncio.get_running_loop().time() + 10
    while not h.notebooks.executions(nid):
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(0.05)
    await h.stop()  # the worker dies while the receipt is still running
    with h.application.bookkeeping() as db:
        db.execute("UPDATE agent_runs SET state='running', owner='dead' WHERE id=?", (run['id'],))
    await h.start()
    assert (await h.settle(run['id']))['state'] == 'interrupted'
    current = task(h, roadmap, 't1')
    assert current['status'] == 'blocked' and 'interrupted by a server restart' in current['explanation']


# ------------------------------------------------------------------ owner changes win

async def test_owner_task_edit_during_the_run_is_never_overwritten(make):
    h = await make(Roles(coder=[insert(), execute_inserted, reply('Done.')]))
    nid, project, conversation, roadmap = setup(h)
    run = await started(h, roadmap)
    await h.settle(run['id'])
    current = h.application.get(roadmap['id'], 'roadmap')
    first = next(t for t in current['data']['tasks'] if t['id'] == 't1')
    h.application.roadmap_update(roadmap['id'], m.TaskUpdate(operation_id=op(), revision=current['revision'], task=m.Task.model_validate(
        {**{k: first[k] for k in m.Task.model_fields}, 'explanation': 'Owner: use the 3-state matrix instead'})), True)
    assert await approve_all(h, run['id']) == 'completed'
    after = task(h, roadmap, 't1')
    assert (after['status'], after['explanation'], after['evidence']) == ('in_progress', 'Owner: use the 3-state matrix instead', [])
    (skipped,) = events(h, run['id'], 'task_update_skipped')
    assert skipped['data']['operation_id'] == 'agent-complete-' + run['id'] and 'the change was kept' in skipped['data']['reason']
    assert activities(h, project) == []


# ------------------------------------------------------------------ REST cannot attribute completion to the agent

async def test_rest_cannot_record_coding_agent_acceptance_or_notes_evidence(make):
    h = await make()
    nid, project, conversation, roadmap = setup(h)
    cell = h.notebooks.open(nid)['cells'][0]['id']
    base = {k: v for k, v in TASKS[2].items()}
    body = {'operation_id': op(), 'revision': roadmap['revision'],
            'task': {**base, 'status': 'completed', 'accepted': True, 'evidence': [{'summary': 'Owner checked'}]}}
    accepted = await h.http('put', f"roadmaps/{roadmap['id']}/tasks/t3", json=body)
    assert accepted.status_code == 200, accepted.text
    assert next(t for t in accepted.json()['data']['tasks'] if t['id'] == 't3')['accepted_by'] == 'user'
    revision = accepted.json()['revision']
    forged = await h.http('put', f"roadmaps/{roadmap['id']}/tasks/t1", json={'operation_id': op(), 'revision': revision,
        'task': {**TASKS[0], 'status': 'completed', 'accepted': True, 'accepted_by': 'coding_agent', 'evidence': [{'summary': 'x'}]}})
    assert forged.status_code == 422
    notes = await h.http('put', f"roadmaps/{roadmap['id']}/tasks/t1", json={'operation_id': op(), 'revision': revision,
        'task': {**TASKS[0], 'status': 'completed', 'accepted': True, 'evidence': [{'summary': 'x', 'cell_ids': [cell]}]}})
    assert notes.status_code == 422 and 'recorded only by the server' in notes.json()['detail']
    assert task(h, roadmap, 't1')['status'] == 'pending'


# ------------------------------------------------------------------ Re-propose a stale cell proposal (owner addition)

STALE = 'The notebook changed since this was proposed. Re-read it and propose again.'


def owner_edit(h, nid, text):
    document = h.notebooks.open(nid)
    owner_cell = document['cells'][0]['id']
    return h.notebooks.edit_cell(nid, document['metadata']['europa']['revision'], 'edit', owner_cell, text=text)


async def test_repropose_on_a_stale_card_is_a_conflict_and_the_run_continues(make):
    from agents.demo import DemoProvider  # noqa: F401 (documentation of the intent: scripted here)
    h = await make(Roles(coder=[insert(), insert(), reply('Inserted after the owner edit.')]))
    nid, project, conversation, roadmap = setup(h)
    run = await started(h, roadmap)
    await h.settle(run['id'])
    stale = h.pending_approval(run['id'])
    owner_edit(h, nid, 'owner_cell = 2')
    body = {'operation_id': op(), 'decision': 'repropose', 'digest': stale['digest']}
    first = await h.http('post', f"agent/approvals/{stale['id']}", json=body)
    replay = await h.http('post', f"agent/approvals/{stale['id']}", json=body)
    assert first.status_code == replay.status_code == 200 and first.json() == replay.json() and first.json()['state'] == 'denied'
    assert (await h.settle(run['id']))['state'] == 'waiting_for_approval'  # the run continued
    view = (await h.http('get', f"agent/runs/{run['id']}")).json()
    assert [(a['state'], a['reason']) for a in view['approvals']] == [('denied', 'stale_revision'), ('pending', None)]
    decided = [e for e in h.runtime.store.events(run['id'], 0, 200)['items'] if e['type'] == 'approval_decided']
    assert decided[0]['data'] == {'approval_id': stale['id'], 'decision': 'denied', 'reason': 'stale_revision'}
    tool_message = [x for x in h.provider.calls[1]['messages'] if x['role'] == 'tool'][-1]
    assert __import__('json').loads(tool_message['content']) == {'status': 'conflict', 'summary': STALE}
    fresh = h.pending_approval(run['id'])
    assert fresh['revision'] == h.notebooks.open(nid)['metadata']['europa']['revision'] != stale['revision']
    assert await approve_all(h, run['id']) == 'completed'
    assert [c['source'] for c in h.notebooks.open(nid)['cells']] == ['owner_cell = 2', MATRIX_CODE]
    record = h.runtime.store.get_run(run['id'])
    assert record['usage']['repairs'] == 0
    current = task(h, roadmap, 't1')  # the run went on: no "every proposal denied" reset to pending
    assert current['status'] == 'in_progress' and 'never ran successfully' in current['explanation']


async def test_repropose_on_a_current_card_is_refused_and_changes_nothing(make):
    h = await make(Roles(coder=[insert()]))
    nid, project, conversation, roadmap = setup(h)
    run = await started(h, roadmap)
    await h.settle(run['id'])
    approval = h.pending_approval(run['id'])
    refused = await h.http('post', f"agent/approvals/{approval['id']}", json={'operation_id': op(), 'decision': 'repropose', 'digest': approval['digest']})
    assert refused.status_code == 409
    assert h.pending_approval(run['id'])['id'] == approval['id'] and h.runtime.store.get_run(run['id'])['state'] == 'waiting_for_approval'
    assert h.runtime.store.tool_calls(run['id'])[0]['state'] == 'awaiting_approval'


async def test_demo_coder_reproposes_once_then_reports_honestly(make):
    from agents.demo import DemoProvider
    h = await make(provider=DemoProvider())
    nid, project, conversation, roadmap = setup(h)
    run = await started(h, roadmap)
    for text in ('owner_cell = 2', 'owner_cell = 3'):
        await h.settle(run['id'])
        approval = h.pending_approval(run['id'])
        assert approval['tool'] == 'insert_code_cell'
        owner_edit(h, nid, text)
        await h.runtime.decide(approval['id'], operation_id=op(), decision='repropose', digest_value=approval['digest'])
    assert (await h.settle(run['id']))['state'] == 'completed'
    assert [c['name'] for c in h.runtime.store.tool_calls(run['id'])] == ['insert_code_cell', 'read_notebook', 'insert_code_cell']
    assert 'The notebook changed again before my proposal could be approved' in h.messages(conversation)[-1]['data']['content']
    assert [c['source'] for c in h.notebooks.open(nid)['cells']] == ['owner_cell = 3']
