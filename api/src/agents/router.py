"""Loopback owner API for durable runs; no model may call these controls."""
import asyncio
from typing import Annotated
from fastapi import APIRouter, Query, Request
from agents.models import Control, Decision, Identity, RunCreate, Scope
from agents.store import AgentFault

router = APIRouter(prefix='/agents')


def service(request): return request.app.state.agent_runtime


@router.get('/configuration')
async def configuration(request: Request):
    return service(request).config()


@router.get('/conversation')
async def conversation(request: Request, scope: Annotated[Scope, Query()]):
    return await asyncio.to_thread(service(request).store.conversation, scope)


@router.post('/runs', status_code=202)
async def start_run(request: Request, body: RunCreate):
    return await service(request).create(body)


@router.get('/runs')
async def runs(request: Request, conversation_id: Identity, before: int = Query(0, ge=0, le=2**63-1), limit: int = Query(20, ge=1, le=50)):
    return await asyncio.to_thread(service(request).store.list, conversation_id, before, limit)


@router.get('/runs/{run}')
async def get_run(request: Request, run: Identity):
    return await asyncio.to_thread(service(request).store.get, run)


@router.get('/runs/{run}/executions')
async def executions(request: Request, run: Identity):
    return await asyncio.to_thread(service(request).coding.executions, run)


@router.post('/runs/{run}/reconcile')
async def reconcile(request: Request, run: Identity, body: Control):
    return await asyncio.to_thread(service(request).coding.reconcile, run, body)


@router.get('/runs/{run}/events')
async def events(request: Request, run: Identity, after: int = Query(0, ge=0, le=2**63-1), limit: int = Query(30, ge=1, le=100)):
    return await asyncio.to_thread(service(request).store.events, run, after, limit)


@router.post('/runs/{run}/approvals/{approval}')
async def decide(request: Request, run: Identity, approval: Identity, body: Decision):
    result = await service(request).decide(run, approval, body)
    if result['decision'] == 'expired': raise AgentFault('Approval expired', 409)
    return result


@router.post('/runs/{run}/cancel')
async def cancel(request: Request, run: Identity, body: Control):
    return await service(request).control(run, body, 'cancel')


@router.post('/runs/{run}/resume')
async def resume(request: Request, run: Identity, body: Control):
    return await service(request).control(run, body, 'resume')


@router.get('/conversations/{conversation}/messages')
async def recent_messages(request: Request, conversation: Identity, before: int = Query(0,ge=0,le=2**63-1), limit: int = Query(30,ge=1,le=50)):
    return await asyncio.to_thread(service(request).store.messages,conversation,before,limit)


@router.get('/conversations/{conversation}/handoffs')
async def handoffs(request: Request, conversation: Identity, limit: int = Query(20,ge=1,le=50)):
    return await asyncio.to_thread(service(request).store.handoffs,conversation,limit)
