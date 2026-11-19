"""Phase 4D Stage 2 with a real local Gateway (opt-in: --run-local-gateway; trusted code only).
PRD test 13: cancel during a long run, a lost acknowledgement and an unknown outcome each settle
from the execution receipt, and no execution is ever replayed."""
import asyncio
import time

import httpx
import pytest

from agents.providers import FakeProvider
from main import create_app
from runtime.local import LocalRuntimeProvider
from agent_support import call, op, reply
from test_agent_coding import Roles

pytestmark = pytest.mark.integration


def execute_inserted(last):
    return call('execute_cell', cell_id=last['data']['cell_id'], explanation='Run it')


class Session:
    def __init__(self, app, http):
        self.app, self.http = app, http

    async def post(self, path, body, expected=(200, 201)):
        response = await self.http.post('/api' + path, json=body)
        assert response.status_code in expected, response.text
        return response.json()

    async def run(self, run_id):
        return (await self.http.get(f'/api/agent/runs/{run_id}')).json()

    async def settle(self, run_id, states=('completed', 'failed', 'cancelled', 'interrupted', 'waiting_for_approval'), timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            view = await self.run(run_id)
            if view['state'] in states: return view
            await asyncio.sleep(0.1)
        raise AssertionError(f"run stuck in {view['state']}")

    async def approve(self, run_id):
        view = await self.settle(run_id, ('waiting_for_approval',))
        (approval,) = [a for a in view['approvals'] if a['state'] == 'pending']
        await self.post(f"/agent/approvals/{approval['id']}", {'operation_id': op(), 'decision': 'approve', 'digest': approval['digest']})
        return approval

    async def setup(self, folder):
        await self.post('/workspace/projects', {'path': folder, 'operation_id': op()})
        created = await self.post('/workspace/notebooks', {'path': f'{folder}/chain.ipynb', 'operation_id': op()})
        notebook, project = created['notebook_id'], created['project_id']
        roadmap = await self.post('/roadmaps', {'operation_id': op(), 'project_id': project, 'notebook_id': notebook, 'objective': 'Run code',
                                                'tasks': [{'id': 't1', 'title': 'Define a transition matrix', 'acceptance_criteria': ['Runs']}]})
        run = await self.post('/agent/tasks/assign', {'operation_id': op(), 'roadmap_id': roadmap['id'], 'task_id': 't1'})
        return notebook, run

    def receipts(self, notebook):
        return self.app.state.notebook_store.executions(notebook)


@pytest.fixture
def roots(tmp_path, monkeypatch):
    for name, path in (('EUROPA_NOTEBOOK_ROOT', 'notebooks'), ('EUROPA_APPLICATION_ROOT', 'application'),
                       ('EUROPA_RUNTIME_REGISTRY', 'runtime.sqlite3')):
        monkeypatch.setenv(name, str(tmp_path / path))
    monkeypatch.setenv('EUROPA_GATEWAY_URL', '')
    (tmp_path / 'workspace').mkdir()
    return tmp_path


async def session(roots, coder, body):
    app = create_app(runtime_provider=LocalRuntimeProvider(root=roots), workspace_root=roots / 'workspace',
                     agent_provider=FakeProvider(Roles(coder=coder)), agent_options={'poll_interval': 0.05})
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://europa', timeout=60) as http:
            await body(Session(app, http))


async def test_cancel_during_a_long_run_settles_from_the_receipt(roots):
    code = "import time\ntime.sleep(4)\nprint('slept')"

    async def body(s):
        notebook, run = await s.setup('Cancel Study')
        await s.approve(run['id'])                # insert
        await s.approve(run['id'])                # execute
        deadline = time.monotonic() + 30
        while not s.receipts(notebook) or s.receipts(notebook)[0]['state'] not in ('running', 'completed', 'applied'):
            assert time.monotonic() < deadline
            await asyncio.sleep(0.05)
        cancelled = await s.post(f"/agent/runs/{run['id']}/cancel", {'operation_id': op()})
        assert cancelled['state'] == 'cancelling'  # Python keeps running; the step settles first
        done = await s.settle(run['id'], ('cancelled', 'completed', 'failed', 'interrupted'))
        assert done['state'] == 'cancelled'
        (receipt,) = s.receipts(notebook)
        assert receipt['state'] == 'applied' and receipt['result']['outputs'][0]['text'] == 'slept\n'
        (execute,) = [c for c in done['tool_calls'] if c['name'] == 'execute_cell']
        assert execute['state'] == 'succeeded' and execute['links']['execution_receipt_id'] == receipt['execution_id'] == execute['id']
    await session(roots, [call('insert_code_cell', source=code, explanation='x'), execute_inserted, reply('should not run')], body)


async def test_lost_acknowledgement_binds_the_receipt_by_id(roots):
    async def body(s):
        service = s.app.state.notebook_service
        original = service.submit

        async def lossy(*args, **kwargs):
            await original(*args, **kwargs)
            raise ConnectionError('acknowledgement lost')
        service.submit = lossy
        notebook, run = await s.setup('Lost Study')
        await s.approve(run['id'])
        await s.approve(run['id'])
        done = await s.settle(run['id'], ('completed', 'failed', 'cancelled', 'interrupted'))
        assert done['state'] == 'completed'
        (receipt,) = s.receipts(notebook)
        (execute,) = [c for c in done['tool_calls'] if c['name'] == 'execute_cell']
        assert receipt['state'] == 'applied' and receipt['execution_id'] == execute['id']
        report = (await s.http.get(f"/api/conversations/{done['conversation_id']}/messages")).json()['items'][-1]['data']
        assert report['execution_receipt_id'] == receipt['execution_id'] and 'row sum = 1.0' in report['content']
    from agents.demo import task_code
    await session(roots, [call('insert_code_cell', source=task_code('Define a transition matrix'), explanation='x'),
                          execute_inserted, reply('Ran it.')], body)


async def test_unknown_outcome_is_reported_from_the_receipt_and_never_replayed(roots):
    async def body(s):
        notebook, run = await s.setup('Unknown Study')
        await s.approve(run['id'])
        await s.approve(run['id'])
        deadline = time.monotonic() + 30
        while not s.receipts(notebook) or s.receipts(notebook)[0]['state'] != 'running':
            assert time.monotonic() < deadline
            await asyncio.sleep(0.05)
        # The execution owner loses track mid-run (e.g. a dropped connection): the outcome is unknown.
        s.app.state.notebook_service.tasks[notebook].cancel()
        done = await s.settle(run['id'], ('completed', 'failed', 'cancelled', 'interrupted'))
        (receipt,) = s.receipts(notebook)
        assert receipt['state'] == 'uncertain'
        (execute,) = [c for c in done['tool_calls'] if c['name'] == 'execute_cell']
        assert execute['links']['execution_receipt_id'] == receipt['execution_id'] and 'uncertain' in execute['summary']
        report = (await s.http.get(f"/api/conversations/{done['conversation_id']}/messages")).json()['items'][-1]['data']
        assert f"receipt {receipt['execution_id'][:8]} is uncertain; no output is claimed" in report['content']
        assert 'Observed output' not in report['content']
    await session(roots, [call('insert_code_cell', source="import time\ntime.sleep(20)\nprint('late')", explanation='x'),
                          execute_inserted, reply('The outcome is unknown.')], body)
