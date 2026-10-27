"""Owner-only application REST APIs. No route dispatches code or provisions runtimes."""
import asyncio
from typing import Annotated
from fastapi import APIRouter, Query, Request
from application import models as m

router = APIRouter()
Cursor = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]


def store(request): return request.app.state.application_store


@router.post('/projects', status_code=201, response_model=m.Record)
async def project_create(request: Request, body: m.ProjectCreate):
    return await asyncio.to_thread(store(request).project_create, body)


@router.get('/projects', response_model=m.Page)
async def projects(request: Request, after: Cursor = 0, limit: Limit = 50):
    return await asyncio.to_thread(store(request).listing, 'project', after=after, limit=limit)


@router.get('/projects/{project}', response_model=m.Record)
async def project_get(project: str, request: Request):
    return await asyncio.to_thread(store(request).get, project, 'project')


@router.patch('/projects/{project}', response_model=m.Record)
async def project_update(project: str, request: Request, body: m.ProjectUpdate):
    return await asyncio.to_thread(store(request).project_update, project, body)


@router.delete('/projects/{project}', response_model=m.Record)
async def project_delete(project: str, request: Request, body: m.Revision):
    return await asyncio.to_thread(store(request).project_delete, project, body)


@router.post('/projects/{project}/notebooks', response_model=m.Record)
async def membership(project: str, request: Request, body: m.Membership):
    return await asyncio.to_thread(store(request).membership, project, body)


@router.get('/projects/{project}/notebooks', response_model=list[m.NotebookMember])
async def members(project: str, request: Request):
    record = await asyncio.to_thread(store(request).get, project, 'project')
    return record['data']['notebooks']


@router.post('/conversations', status_code=201, response_model=m.Record)
async def conversation_create(request: Request, body: m.ConversationCreate):
    return await asyncio.to_thread(store(request).conversation_create, body)


@router.get('/conversations', response_model=m.Page)
async def conversations(request: Request, project_id: str, after: Cursor = 0, limit: Limit = 50):
    return await asyncio.to_thread(store(request).listing, 'conversation', project=project_id, after=after, limit=limit)


@router.get('/conversations/{conversation}', response_model=m.Record)
async def conversation_get(conversation: str, request: Request):
    return await asyncio.to_thread(store(request).get, conversation, 'conversation')


@router.post('/conversations/{conversation}/messages', status_code=201, response_model=m.Record)
async def message_append(conversation: str, request: Request, body: m.MessageCreate):
    return await asyncio.to_thread(store(request).message_append, conversation, body)


@router.get('/conversations/{conversation}/messages', response_model=m.Page)
async def messages(conversation: str, request: Request, after: Cursor = 0, limit: Limit = 50):
    return await asyncio.to_thread(store(request).listing, 'message', parent=conversation, after=after, limit=limit)


@router.post('/roadmaps', status_code=201, response_model=m.Record)
async def roadmap_create(request: Request, body: m.RoadmapCreate):
    return await asyncio.to_thread(store(request).roadmap_create, body)


@router.get('/roadmaps', response_model=m.Page)
async def roadmaps(request: Request, project_id: str, after: Cursor = 0, limit: Limit = 50):
    return await asyncio.to_thread(store(request).listing, 'roadmap', project=project_id, after=after, limit=limit)


@router.get('/roadmaps/{roadmap}', response_model=m.Record)
async def roadmap_get(roadmap: str, request: Request):
    return await asyncio.to_thread(store(request).get, roadmap, 'roadmap')


@router.put('/roadmaps/{roadmap}', response_model=m.Record)
async def roadmap_update(roadmap: str, request: Request, body: m.RoadmapUpdate):
    return await asyncio.to_thread(store(request).roadmap_update, roadmap, body)


@router.get('/roadmaps/{roadmap}/versions', response_model=m.VersionPage)
async def versions(roadmap: str, request: Request, after: Cursor = 0, limit: Limit = 50):
    return await asyncio.to_thread(store(request).versions, roadmap, after, limit)


@router.get('/roadmaps/{roadmap}/tasks', response_model=list[m.TaskView])
async def tasks(roadmap: str, request: Request):
    record = await asyncio.to_thread(store(request).get, roadmap, 'roadmap')
    return record['data']['tasks']


@router.put('/roadmaps/{roadmap}/tasks/{task}', response_model=m.Record)
async def task_update(roadmap: str, task: str, request: Request, body: m.TaskUpdate):
    from application.store import ApplicationError
    if body.task.id != task: raise ApplicationError('Task ID must match path')
    return await asyncio.to_thread(store(request).roadmap_update, roadmap, body, True)


@router.post('/notebooks/{notebook}/comments', status_code=201, response_model=m.Record)
async def comment_create(notebook: str, request: Request, body: m.CommentCreate):
    return await asyncio.to_thread(store(request).comment_create, notebook, body)


@router.get('/notebooks/{notebook}/comments', response_model=m.Page)
async def comments(notebook: str, request: Request, after: Cursor = 0, limit: Limit = 50):
    return await asyncio.to_thread(store(request).listing, 'comment', notebook=notebook, after=after, limit=limit)


@router.patch('/comments/{comment}', response_model=m.Record)
async def resolve(comment: str, request: Request, body: m.Resolve):
    return await asyncio.to_thread(store(request).resolve, comment, body)


@router.get('/comments/{comment}', response_model=m.Record)
async def comment_get(comment: str, request: Request):
    return await asyncio.to_thread(store(request).get, comment, 'comment')


@router.post('/projects/{project}/activities', status_code=201, response_model=m.Record)
async def activity_append(project: str, request: Request, body: m.ActivityCreate):
    from application.store import ApplicationError
    if body.event_type != 'user_intervention':
        raise ApplicationError('This event type requires server-controlled authorship', 403)
    return await asyncio.to_thread(store(request).activity_append, project, body)


@router.get('/projects/{project}/activities', response_model=m.Page)
async def activities(project: str, request: Request, after: Cursor = 0, limit: Limit = 50):
    return await asyncio.to_thread(store(request).listing, 'activity', project=project, after=after, limit=limit)
