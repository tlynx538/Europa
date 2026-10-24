"""Document APIs return standard notebooks; revisions live in metadata.europa."""
import asyncio
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt

router = APIRouter(prefix='/notebooks')


class Revision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: StrictInt = Field(ge=1)


class Create(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(default='Untitled', min_length=1, max_length=256)


class Save(Revision):
    document: dict


class Rename(Revision):
    title: str = Field(min_length=1, max_length=256)


class Insert(Revision):
    cell_type: str = 'code'
    source: str = Field(default='', max_length=1_000_000)
    metadata: dict = Field(default_factory=dict)
    index: StrictInt | None = None


class Edit(Revision):
    source: str = Field(max_length=1_000_000)
    metadata: dict | None = None


class Move(Revision):
    index: StrictInt


class Type(Revision):
    cell_type: str


class Execute(Revision):
    kernel_id: str | None = Field(default=None, pattern=r'^[a-zA-Z0-9-]{1,128}$')
    timeout: float = Field(default=30, gt=0, le=300)


def store(request): return request.app.state.notebook_store


@router.post('', status_code=201)
async def create(request: Request, body: Create):
    return await asyncio.to_thread(store(request).create, body.title)


@router.get('')
async def listing(request: Request):
    return await asyncio.to_thread(store(request).list)


@router.get('/{notebook}')
async def opened(notebook: str, request: Request):
    return await asyncio.to_thread(store(request).open, notebook)


@router.put('/{notebook}')
async def save(notebook: str, request: Request, body: Save):
    return await asyncio.to_thread(store(request).save, notebook, body.revision, body.document)


@router.patch('/{notebook}')
async def rename(notebook: str, request: Request, body: Rename):
    return await asyncio.to_thread(store(request).rename, notebook, body.revision, body.title)


@router.delete('/{notebook}', status_code=204)
async def delete(notebook: str, request: Request, revision: Annotated[int, Query(ge=1)]):
    await asyncio.to_thread(store(request).delete, notebook, revision)
    return Response(status_code=204)


@router.post('/{notebook}/cells', status_code=201)
async def insert(notebook: str, request: Request, body: Insert):
    return await asyncio.to_thread(store(request).edit_cell, notebook, body.revision, 'insert',
        cell_type=body.cell_type, text=body.source, metadata=body.metadata, index=body.index)


@router.get('/{notebook}/cells/{cell}')
async def get_cell(notebook: str, cell: str, request: Request):
    document = await asyncio.to_thread(store(request).open, notebook)
    return {'revision': document['metadata']['europa']['revision'], 'cell': store(request).cell(document, cell)}


@router.patch('/{notebook}/cells/{cell}')
async def edit(notebook: str, cell: str, request: Request, body: Edit):
    return await asyncio.to_thread(store(request).edit_cell, notebook, body.revision, 'edit', cell,
                                   text=body.source, metadata=body.metadata)


@router.delete('/{notebook}/cells/{cell}')
async def delete_cell(notebook: str, cell: str, request: Request, revision: Annotated[int, Query(ge=1)]):
    return await asyncio.to_thread(store(request).edit_cell, notebook, revision, 'delete', cell)


@router.post('/{notebook}/cells/{cell}/move')
async def move(notebook: str, cell: str, request: Request, body: Move):
    return await asyncio.to_thread(store(request).edit_cell, notebook, body.revision, 'move', cell, index=body.index)


@router.post('/{notebook}/cells/{cell}/type')
async def change_type(notebook: str, cell: str, request: Request, body: Type):
    return await asyncio.to_thread(store(request).edit_cell, notebook, body.revision, 'type', cell, cell_type=body.cell_type)


@router.post('/{notebook}/cells/{cell}/clear')
async def clear(notebook: str, cell: str, request: Request, body: Revision):
    return await asyncio.to_thread(store(request).edit_cell, notebook, body.revision, 'clear', cell)


@router.post('/{notebook}/cells/{cell}/execute', status_code=202)
async def execute(notebook: str, cell: str, request: Request, body: Execute):
    return await request.app.state.notebook_service.submit(notebook, cell, body.revision,
                                                         kernel_id=body.kernel_id, timeout=body.timeout)


@router.get('/{notebook}/executions/{execution}')
async def get_execution(notebook: str, execution: str, request: Request):
    return await asyncio.to_thread(store(request).execution, notebook, execution)
