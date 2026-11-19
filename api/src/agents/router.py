"""Agent REST API. Clients create runs, decide approvals and cancel; the backend runs agents.

The request bodies cannot select a role, capability, author, budget or tool.
"""
import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from application.store import ApplicationError

router = APIRouter(prefix='/agent')
OperationId = Annotated[str, Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')]


class Body(BaseModel):
    model_config = ConfigDict(extra='forbid')


class StartRun(Body):
    operation_id: OperationId
    conversation_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    message_id: str = Field(pattern=r'^[a-f0-9]{32}$')


class Decision(Body):
    operation_id: OperationId
    decision: Literal['approve', 'deny', 'repropose']
    digest: str = Field(pattern=r'^[a-f0-9]{64}$')


class Operation(Body):
    operation_id: OperationId


class Assign(Body):
    operation_id: OperationId
    roadmap_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    task_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    instructions: str | None = Field(default=None, max_length=4000)


def runtime(request):
    value = getattr(request.app.state, 'agent_runtime', None)
    if value is None:
        raise ApplicationError(getattr(request.app.state, 'agent_disabled_reason', None) or 'Agents are not configured', 503)
    return value


def view(store, run, *, detail=True):
    public = {key: run[key] for key in ('id', 'role', 'state', 'conversation_id', 'trigger_message_id', 'notebook_id',
                                        'project_id', 'parent_run_id', 'depth', 'provider', 'model', 'budget', 'usage',
                                        'error', 'result', 'created_at', 'updated_at', 'started_at', 'finished_at')}
    task = run['task']
    # A kickoff run's task holds the Home purpose; only the kickoff flag is public.
    public['task'] = None if task is None else {'kickoff': True} if task.get('kickoff') else {
        k: task.get(k) for k in ('roadmap_id', 'task_id', 'title', 'assigned_by')}
    if detail:
        public['approvals'] = [{key: a.get(key) for key in ('id', 'tool', 'state', 'digest', 'preview', 'revision',
                                                             'created_at', 'expires_at', 'decided_at', 'reason')}
                               for a in store.approvals(run['id'])]
        public['tool_calls'] = [{'id': c['id'], 'name': c['name'], 'state': c['state'],
                                 'summary': (c['result'] or {}).get('summary'), 'status': (c['result'] or {}).get('status'),
                                 'links': (c['result'] or {}).get('links'), 'data': _public_data(c)}
                                for c in store.tool_calls(run['id'])]
    return public


def _public_data(call):
    # Only identities useful for navigation; never transcripts or raw outputs.
    data = (call['result'] or {}).get('data') or {}
    return {k: data[k] for k in ('notebook_id', 'notebook_path', 'project_id', 'child_run_id', 'execution_id', 'cell_id',
                                 'state', 'status', 'output', 'roadmap_id') if k in data} or None


@router.get('/status')
async def status(request: Request):
    value = getattr(request.app.state, 'agent_runtime', None)
    if value is None:
        return {'enabled': False, 'provider': None, 'model': None,
                'reason': getattr(request.app.state, 'agent_disabled_reason', None) or 'Agents are not configured'}
    return value.status()


@router.get('/home/conversation')
async def home_conversation(request: Request):
    runtime(request)  # Disabled agents leave Home untouched: no conversation is created.
    return await asyncio.to_thread(request.app.state.application_store.ensure_workspace_conversation)


@router.post('/home/conversations', status_code=201)
async def new_home_conversation(request: Request, body: Operation):
    runtime(request)
    return await asyncio.to_thread(request.app.state.application_store.workspace_conversation_create, body.operation_id)


@router.post('/runs', status_code=201)
async def start_run(request: Request, body: StartRun):
    rt = runtime(request)
    run = await rt.start_run(operation_id=body.operation_id, conversation_id=body.conversation_id, message_id=body.message_id)
    return await asyncio.to_thread(view, rt.store, run)


@router.post('/tasks/assign', status_code=201)
async def assign_task(request: Request, body: Assign):
    """Owner assignment of one roadmap task to the Coding Agent. The server checks the same
    readiness rules as an approved Coordinator delegation; an exact replay returns the same run."""
    rt = runtime(request)
    run = await rt.assign_task(operation_id=body.operation_id, roadmap_id=body.roadmap_id, task_id=body.task_id,
                               instructions=body.instructions)
    return await asyncio.to_thread(view, rt.store, run)


@router.get('/runs')
async def runs(request: Request, conversation_id: Annotated[str, Query(pattern=r'^[a-f0-9]{32}$')]):
    rt = runtime(request)

    def work():
        return {'items': [view(rt.store, run) for run in rt.store.runs_for_conversation(conversation_id)]}
    return await asyncio.to_thread(work)


@router.get('/runs/{run_id}')
async def run_get(run_id: str, request: Request):
    rt = runtime(request)
    return await asyncio.to_thread(lambda: view(rt.store, rt.store.get_run(run_id)))


@router.get('/runs/{run_id}/events')
async def events(run_id: str, request: Request, after: Annotated[int, Query(ge=0)] = 0,
                 limit: Annotated[int, Query(ge=1, le=200)] = 100):
    rt = runtime(request)
    return await asyncio.to_thread(rt.store.events, run_id, after, limit)


@router.post('/runs/{run_id}/cancel')
async def cancel(run_id: str, request: Request, body: Operation):
    rt = runtime(request)
    run = await rt.cancel(run_id)
    return await asyncio.to_thread(view, rt.store, run)


@router.post('/approvals/{approval_id}')
async def decide(approval_id: str, request: Request, body: Decision):
    rt = runtime(request)
    approval = await rt.decide(approval_id, operation_id=body.operation_id, decision=body.decision, digest_value=body.digest)
    return {key: approval[key] for key in ('id', 'run_id', 'state', 'decided_at')}
