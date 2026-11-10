"""Root-confined workspace browsing and journaled mutations."""
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from workspace.browser import WorkspaceBrowser
from workspace.mutation import WorkspaceMutation

router = APIRouter()


class CreateBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=4096)


class SaveBody(CreateBody):
    revision: str = Field(min_length=64, max_length=64)
    content: str = Field(max_length=1024 * 1024)


def open_notebook(request, body, create):
    store = request.app.state.notebook_store
    notebook = store.associate(body.path, body.operation_id, create=create)
    location = store.location(notebook)
    if location.get('project_id'):
        project = request.app.state.workspace.project(location['project_path'])
        if project['id'] != location['project_id']:
            from workspace.browser import WorkspaceError
            raise WorkspaceError(409, 'Project binding changed')
    request.app.state.application_store.bind_physical_notebook(notebook, location)
    return {'notebook_id': notebook, **location}


@router.post('/workspace/notebooks/open')
async def associate_notebook(request: Request, body: CreateBody):
    return await run_in_threadpool(open_notebook, request, body, False)


@router.post('/workspace/notebooks', status_code=201)
async def create_notebook(request: Request, body: CreateBody):
    return await run_in_threadpool(open_notebook, request, body, True)


@router.get('/workspace/notebooks/{notebook}/location')
async def notebook_location(request: Request, notebook: str):
    return await run_in_threadpool(request.app.state.notebook_store.location, notebook)


def mutation(request: Request) -> WorkspaceMutation:
    if not hasattr(request.app.state, 'workspace_mutation'):
        from workspace.browser import WorkspaceError
        raise WorkspaceError(503, 'Workspace write journal unavailable')
    return request.app.state.workspace_mutation


@router.get('/workspace')
async def list_workspace(request: Request, path: str = '', cursor: str | None = None,
                         limit: int = Query(100, ge=1, le=100)):
    browser: WorkspaceBrowser = request.app.state.workspace
    return await run_in_threadpool(browser.list, path, cursor, limit)


@router.get('/workspace/text')
async def read_workspace_text(request: Request, path: str):
    browser: WorkspaceBrowser = request.app.state.workspace
    return await run_in_threadpool(browser.read_text, path)


@router.get('/workspace/project')
async def inspect_workspace_project(request: Request, path: str):
    browser: WorkspaceBrowser = request.app.state.workspace
    return await run_in_threadpool(browser.project, path)


@router.post('/workspace/projects', status_code=201)
async def create_workspace_project(request: Request, body: CreateBody):
    return await run_in_threadpool(mutation(request).create_project, body.model_dump())


@router.post('/workspace/folders', status_code=201)
async def create_folder(request: Request, body: CreateBody):
    return await run_in_threadpool(mutation(request).create, 'folder', body.model_dump())


@router.post('/workspace/files', status_code=201)
async def create_file(request: Request, body: CreateBody):
    return await run_in_threadpool(mutation(request).create, 'file', body.model_dump())


@router.post('/workspace/text/save')
async def save_text(request: Request, body: SaveBody):
    return await run_in_threadpool(mutation(request).save, body.model_dump())
