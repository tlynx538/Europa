"""PRD test 12 (opt-in: --run-local-gateway; trusted code only): the full Phase 4D journey through
the real app lifespan with the deterministic DemoProvider and a real local Gateway, then a restart."""
import asyncio
import time

import httpx
import pytest

from agents.demo import DemoProvider
from main import create_app
from runtime.local import LocalRuntimeProvider
from agent_support import op
from test_agent_coding_gateway import Session, roots  # noqa: F401 (fixture)

pytestmark = pytest.mark.integration


def lifespan_app(roots):
    return create_app(runtime_provider=LocalRuntimeProvider(root=roots), workspace_root=roots / 'workspace',
                      agent_provider=DemoProvider(), agent_options={'poll_interval': 0.05})


async def wait_runs(s, conversation, predicate, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        runs = (await s.http.get(f'/api/agent/runs?conversation_id={conversation}')).json()['items']
        if predicate(runs): return runs
        await asyncio.sleep(0.1)
    raise AssertionError(f'runs did not settle: {[(r["role"], r["state"]) for r in runs]}')


async def test_home_to_verified_completion_persists_across_restart(roots):
    state = {}
    app = lifespan_app(roots)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://europa', timeout=90) as http:
            s = Session(app, http)
            home = (await http.get('/api/agent/home/conversation')).json()['id']
            message = await s.post(f'/conversations/{home}/messages', {'operation_id': op(), 'content': 'I want to understand a simple Markov chain.'})
            created = await s.post('/agent/runs', {'operation_id': op(), 'conversation_id': home, 'message_id': message['id']})
            await s.approve(created['id'])
            done = await s.settle(created['id'], ('completed', 'failed'))
            assert done['state'] == 'completed'
            data = done['tool_calls'][0]['data']
            notebook, project = data['notebook_id'], data['project_id']
            conversation = (await http.get(f'/api/notebooks/{notebook}/conversation')).json()['id']
            # Kickoff: questions and a draft plan; approve the plan.
            (kickoff,) = await wait_runs(s, conversation, lambda runs: len(runs) == 1 and runs[0]['state'] == 'waiting_for_approval')
            assert kickoff['task'] == {'kickoff': True}
            await s.approve(kickoff['id'])
            assert (await s.settle(kickoff['id'], ('completed', 'failed')))['state'] == 'completed'
            (roadmap,) = (await http.get(f'/api/roadmaps?project_id={project}')).json()['items']
            first = roadmap['data']['tasks'][0]
            assert first['title'] == 'Define a transition matrix'
            # Assign task 1; approve the insert, then the run.
            run = await s.post('/agent/tasks/assign', {'operation_id': op(), 'roadmap_id': roadmap['id'], 'task_id': first['id']})
            await s.approve(run['id'])
            await s.approve(run['id'])
            finished = await s.settle(run['id'], ('completed', 'failed', 'cancelled', 'interrupted'), timeout=120)
            assert finished['state'] == 'completed', finished
            (receipt,) = s.receipts(notebook)
            assert receipt['state'] == 'applied' and receipt['result']['status'] == 'ok'
            assert 'A: row sum = 1.0' in ''.join(o.get('text', '') for o in receipt['result']['outputs'])
            # Verified auto-completion.
            record = (await http.get(f"/api/roadmaps/{roadmap['id']}")).json()
            task = record['data']['tasks'][0]
            assert (task['status'], task['accepted'], task['accepted_by']) == ('completed', True, 'coding_agent')
            assert [e['execution_receipt_id'] for e in task['evidence']] == [receipt['execution_id']]
            assert task['links']['cell_id'] == receipt['cell_id'] and record['data']['progress'] == 25.0
            activity = [a for a in (await http.get(f'/api/projects/{project}/activities')).json()['items']
                        if a['data']['event_type'] == 'task_completed']
            assert [(a['data']['actor'], a['data']['execution_receipt_id']) for a in activity] == [('coding_agent', receipt['execution_id'])]
            state.update(notebook=notebook, project=project, roadmap=record, conversation=conversation, receipt=receipt,
                         runs=[(r['id'], r['state']) for r in (await http.get(f'/api/agent/runs?conversation_id={conversation}')).json()['items']])

    restarted = lifespan_app(roots)
    async with restarted.router.lifespan_context(restarted):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restarted), base_url='http://europa', timeout=60) as http:
            s = Session(restarted, http)
            await asyncio.sleep(1.0)  # give recovery and the scheduler a chance to (wrongly) act
            assert [r['execution_id'] for r in s.receipts(state['notebook'])] == [state['receipt']['execution_id']]
            runs = (await http.get(f"/api/agent/runs?conversation_id={state['conversation']}")).json()['items']
            assert [(r['id'], r['state']) for r in runs] == state['runs']
            record = (await http.get(f"/api/roadmaps/{state['roadmap']['id']}")).json()
            assert record['revision'] == state['roadmap']['revision'] and record['data']['tasks'] == state['roadmap']['data']['tasks']
            document = (await http.get(f"/api/notebooks/{state['notebook']}")).json()
            cell = next(c for c in document['cells'] if c['id'] == state['receipt']['cell_id'])
            assert 'A: row sum = 1.0' in ''.join(o.get('text', '') for o in cell['outputs'])
            assert restarted.state.notebook_service.tasks == {}
