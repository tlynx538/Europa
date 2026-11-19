"""Typed, versioned harness tools.

Each tool declares its argument schema, capability, approval policy, idempotency
contract and timeout. Notebook-role tools take no notebook ID: they always act on
the run's own notebook, so cross-notebook access cannot be expressed.
Side-effecting tools bind their target identity at proposal time (`bind`) and
re-verify it at execution; a stale binding is a conflict, never an overwrite.
"""
import asyncio
from dataclasses import dataclass
import hashlib
import json
import re
import time
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field

from application import models as m
from notebooks.store import NotebookError, source, source_hash
from workspace.browser import WorkspaceError

ANSI = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
TERMINAL_RECEIPTS = {'applied', 'conflict', 'uncertain'}


class ToolError(Exception):
    """A tool outcome the model may react to (validation, conflict, missing target)."""

    def __init__(self, message, kind='error'):
        super().__init__(message)
        self.kind = kind


class Args(BaseModel):
    model_config = ConfigDict(extra='forbid')


def clip(text, limit):
    text = text if isinstance(text, str) else str(text)
    return text if len(text) <= limit else text[:limit] + f'… [{len(text) - limit} more characters omitted]'


def output_text(outputs, limit=1200):
    """Bounded plain-text view of nbformat outputs; never rich markup."""
    parts = []
    for output in outputs or []:
        kind = output.get('output_type')
        if kind == 'stream':
            parts.append(''.join(output.get('text', '')) if isinstance(output.get('text'), list) else output.get('text', ''))
        elif kind in {'execute_result', 'display_data'}:
            data = output.get('data', {})
            text = data.get('text/plain')
            parts.append(''.join(text) if isinstance(text, list) else (text or f'[{", ".join(sorted(data))}]'))
        elif kind == 'error':
            trace = ANSI.sub('', '\n'.join(output.get('traceback', [])[-6:]))
            parts.append(f"{output.get('ename')}: {output.get('evalue')}\n{trace}")
    return clip('\n'.join(p.rstrip('\n') for p in parts), limit)


@dataclass
class ToolContext:
    run: dict
    services: Any
    call_id: str | None = None
    operation_id: str | None = None

    @property
    def notebook_id(self):
        if not self.run.get('notebook_id'):
            raise ToolError('This run has no notebook scope', 'policy')
        return self.run['notebook_id']


@dataclass(frozen=True)
class Tool:
    name: str
    version: int
    description: str
    args: type[Args]
    capability: str
    approval: bool
    idempotency: str  # read | operation_id | marker | content_hash | receipt
    timeout: float
    execute: Callable[[ToolContext, Any, dict | None], Awaitable[dict]]
    bind: Callable[[ToolContext, Any], Awaitable[dict]] | None = None
    preview: Callable[[ToolContext, Any, dict], dict] | None = None
    reconcile: Callable[[ToolContext, Any, dict | None, dict | None], Awaitable[tuple[str, dict | None]]] | None = None

    def spec(self):
        schema = self.args.model_json_schema()
        schema.pop('title', None)
        return {'name': self.name, 'description': self.description, 'parameters': schema}


def run_sync(function, *args, **kwargs):
    return asyncio.to_thread(function, *args, **kwargs)


# --------------------------------------------------------------------------- read tools

class NoArgs(Args):
    pass


class WorkspaceListArgs(Args):
    path: str = Field(default='', max_length=1024, description='Workspace-relative directory; empty for root')


async def list_workspace(ctx, args, _binding):
    page = await run_sync(ctx.services.workspace.list, args.path, None, 100)
    items = [{'name': i['name'], 'type': i['type'], **({'project': True} if i.get('project_id') else {})} for i in page['items']]
    return {'summary': f"Listed {len(items)} entries in {args.path or 'workspace root'}",
            'data': {'path': page['path'], 'entries': items, 'truncated': page.get('next_cursor') is not None}}


async def list_projects(ctx, _args, _binding):
    page = await run_sync(ctx.services.app.listing, 'project', limit=100)
    projects = [{'project_id': p['id'], 'title': p['data']['title'], 'status': p['data']['status'],
                 'notebooks': len([n for n in p['data']['notebooks'] if n['present']])} for p in page['items']]
    return {'summary': f'Listed {len(projects)} projects', 'data': {'projects': projects}}


async def list_notebooks(ctx, _args, _binding):
    items = await run_sync(ctx.services.notebooks.list)
    notebooks = [{'notebook_id': n['notebook_id'], 'title': n['title'], 'revision': n['revision']} for n in items[:100]]
    return {'summary': f'Listed {len(notebooks)} notebooks', 'data': {'notebooks': notebooks}}


def notebook_view(document, *, cells=40, source_limit=2000, output_limit=600):
    all_cells = document['cells']
    shown = all_cells[-cells:]
    return {
        'notebook_id': document['metadata']['europa']['notebook_id'],
        'title': document['metadata']['europa']['title'],
        'revision': document['metadata']['europa']['revision'],
        'cell_count': len(all_cells),
        'omitted_leading_cells': len(all_cells) - len(shown),
        'cells': [{'index': all_cells.index(c), 'cell_id': c['id'], 'type': c['cell_type'],
                   'source': clip(source(c), source_limit),
                   **({'execution_count': c.get('execution_count'),
                       'last_status': c['metadata'].get('europa', {}).get('status'),
                       'output': output_text(c.get('outputs'), output_limit)} if c['cell_type'] == 'code' else {}),
                   **({'agent_authored': True} if c['metadata'].get('europa_agent') else {})}
                  for c in shown],
    }


class ReadNotebookArgs(Args):
    notebook_id: str = Field(pattern=r'^[a-f0-9]{32}$')


async def read_any_notebook(ctx, args, _binding):
    document = await run_sync(ctx.services.notebooks.open, args.notebook_id)
    view = notebook_view(document, cells=12, source_limit=600, output_limit=200)
    return {'summary': f"Read notebook {view['title']} (revision {view['revision']})", 'data': view}


async def read_own_notebook(ctx, _args, _binding):
    document = await run_sync(ctx.services.notebooks.open, ctx.notebook_id)
    view = notebook_view(document)
    return {'summary': f"Read notebook revision {view['revision']} ({view['cell_count']} cells)", 'data': view}


def _project_for(ctx):
    """Current project membership of the run's notebook, or None."""
    with ctx.services.app.transaction():
        row = ctx.services.app.db.execute(
            'SELECT project_id FROM memberships WHERE notebook_id=? AND associated=1', (ctx.notebook_id,)).fetchone()
    return row['project_id'] if row else None


def _notebook_roadmap(ctx, project):
    """The roadmap a notebook role sees: the most recently created roadmap in the
    notebook's current project whose notebook_id is this notebook. Roadmaps of other
    notebooks in the same project (or project-level roadmaps) are never returned."""
    if project is None: return None
    app, latest, cursor = ctx.services.app, None, 0
    while cursor is not None:  # listing is ordered by creation sequence
        page = app.listing('roadmap', project=project, notebook=ctx.notebook_id, after=cursor, limit=100)
        latest = page['items'][-1] if page['items'] else latest
        cursor = page['next_cursor']
    return latest


def roadmap_view(record):
    if record is None: return None
    return {'roadmap_id': record['id'], 'revision': record['revision'], 'objective': record['data']['objective'],
            'progress': record['data']['progress'],
            'tasks': [{'task_id': t['id'], 'title': t['title'], 'status': t['status'], 'dependencies': t['dependencies'],
                       'acceptance_criteria': t['acceptance_criteria'], 'explanation': t['explanation'],
                       'evidence': [e['summary'] for e in t['evidence']][-3:]} for t in record['data']['tasks']]}


async def read_roadmap(ctx, _args, _binding):
    def work():
        project = _project_for(ctx)
        return project, _notebook_roadmap(ctx, project)
    project, record = await run_sync(work)
    if project is None:
        return {'summary': 'Notebook is not in a project; no roadmap available', 'data': {'project_id': None, 'roadmap': None}}
    return {'summary': 'Read roadmap' if record else 'No roadmap exists yet',
            'data': {'project_id': project, 'roadmap': roadmap_view(record)}}


class ReadExecutionArgs(Args):
    execution_id: str = Field(pattern=r'^[a-f0-9]{32}$')


def receipt_view(receipt):
    result = receipt.get('result') or {}
    return {'execution_id': receipt['execution_id'], 'cell_id': receipt['cell_id'], 'state': receipt['state'],
            'status': result.get('status'), 'execution_count': result.get('execution_count'),
            'output': output_text(result.get('outputs'), 2000)}


async def read_execution(ctx, args, _binding):
    receipt = await run_sync(ctx.services.notebooks.execution, ctx.notebook_id, args.execution_id)
    view = receipt_view(receipt)
    return {'summary': f"Receipt {view['execution_id'][:8]}: {view['state']}", 'data': view}


# --------------------------------------------------------------------------- Home: create project + notebook

NAME = r'^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}$'


class CreateProjectNotebookArgs(Args):
    project_name: str | None = Field(default=None, pattern=NAME, description='Project folder name at workspace root; omit for a standalone notebook')
    notebook_name: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}\.ipynb$')
    purpose: str = Field(min_length=1, max_length=1000, description='Why this notebook exists; recorded in its conversation')


def _entry(browser, directory, name, pages=100):
    """Find `name` in a directory across listing pages. A name differing only in
    case is a conflict: case-insensitive filesystems would treat it as the same entry."""
    cursor = None
    for _ in range(pages):
        page = browser.list(directory, cursor, 100)
        for item in page['items']:
            if item['name'] == name:
                return item
            if item['name'].casefold() == name.casefold():
                where = f'{directory}/{item["name"]}' if directory else item['name']
                raise ToolError(f'{where} already exists (differs only in letter case)', 'conflict')
        cursor = page.get('next_cursor')
        if cursor is None:
            return None
    raise ToolError(f"{directory or 'The workspace root'} has too many entries to verify the name is free", 'conflict')


async def bind_create(ctx, args):
    def work():
        browser = ctx.services.workspace
        project_path = args.project_name
        notebook_path = '/'.join(p for p in (project_path, args.notebook_name) if p)
        create_project = False
        if project_path:
            existing = _entry(browser, '', project_path)
            if existing and not existing.get('project_id'):
                raise ToolError(f'A folder or file named {project_path!r} already exists and is not a Europa project', 'conflict')
            create_project = existing is None
            if existing and _entry(browser, project_path, args.notebook_name):
                raise ToolError(f'{notebook_path} already exists', 'conflict')
        elif _entry(browser, '', args.notebook_name):
            raise ToolError(f'{notebook_path} already exists', 'conflict')
        return {'project_path': project_path, 'create_project': create_project, 'notebook_path': notebook_path}
    try:
        return await run_sync(work)
    except WorkspaceError as exc:
        raise ToolError(f'Could not verify the target: {exc}', 'conflict' if exc.status_code == 409 else 'error') from None


def preview_create(_ctx, args, binding):
    actions = []
    if binding['create_project']: actions.append(f"Create project folder {binding['project_path']}/")
    actions.append(f"Create notebook {binding['notebook_path']}")
    return {'title': 'Create resources', 'actions': actions, 'purpose': args.purpose}


async def create_project_notebook(ctx, args, binding):
    services, op = ctx.services, ctx.operation_id

    def work():
        project = None
        if binding['create_project']:
            project = services.mutation.create_project({'operation_id': op + '-p', 'path': binding['project_path']})
        notebook = services.notebooks.associate(binding['notebook_path'], op + '-n', create=True)
        location = services.notebooks.location(notebook)
        if location.get('project_id'):
            if services.workspace.project(location['project_path'])['id'] != location['project_id']:
                raise ToolError('Project binding changed', 'conflict')
        conversation = services.app.bind_physical_notebook(notebook, location)
        origin = ctx.run['conversation_id']
        services.app.message_append(conversation['id'], m.MessageCreate(
            operation_id=op + '-origin', content=clip(
                f'Notebook created from a Home conversation (origin {origin}). Purpose: {args.purpose}', 4000)),
            author_role='coordinator')
        return {'notebook_id': notebook, 'notebook_path': binding['notebook_path'],
                'project_id': location.get('project_id') or (project or {}).get('id'),
                'project_path': location.get('project_path'), 'conversation_id': conversation['id'],
                'origin_conversation_id': origin}
    try:
        result = await run_sync(work)
    except (WorkspaceError, NotebookError) as exc:
        kind = 'conflict' if getattr(exc, 'status_code', 0) == 409 else 'error'
        raise ToolError(f'Workspace creation failed: {exc}', kind) from None
    return {'summary': f"Created {result['notebook_path']}", 'data': result,
            'links': {'notebook_id': result['notebook_id']}}


async def reconcile_create(ctx, args, binding, _partial):
    # Journaled workspace operations replay exactly by operation ID; a pending
    # uncertain mkdir/file creation refuses (409) instead of acting twice.
    try:
        return 'succeeded', await create_project_notebook(ctx, args, binding)
    except ToolError as exc:
        return 'uncertain', {'summary': f'Creation outcome requires inspection: {exc}'}


# --------------------------------------------------------------------------- Notebook Coordinator

NEW_KEY_MAX = 40  # '<8-char prefix>-<key>' stays within the 64-character task-ID limit


class PlanTask(Args):
    # Up to 64 characters so any existing task ID (the task-ID pattern) can be reused
    # as a key; bind_roadmap limits keys of *new* tasks to NEW_KEY_MAX characters.
    key: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$',
                     description='Short key (at most 40 characters) for a new task; reuse an existing task_id to keep that task')
    title: str = Field(min_length=1, max_length=200)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=8)
    depends_on: list[str] = Field(default_factory=list, max_length=16)


class ProposeRoadmapArgs(Args):
    objective: str = Field(min_length=1, max_length=2000)
    tasks: list[PlanTask] = Field(min_length=1, max_length=24)
    rationale: str = Field(default='', max_length=2000)


async def bind_roadmap(ctx, args):
    def work():
        project = _project_for(ctx)
        if project is None:
            raise ToolError('This notebook is not in a project; planning requires a project', 'policy')
        current = _notebook_roadmap(ctx, project)
        existing = {t['id']: t for t in current['data']['tasks']} if current else {}
        keys = [t.key for t in args.tasks]
        if len(set(keys)) != len(keys): raise ToolError('Duplicate task keys')
        prefix = (ctx.call_id or 'plan')[:8]
        long_keys = [t.key for t in args.tasks if t.key not in existing and len(t.key) > NEW_KEY_MAX]
        if long_keys:
            raise ToolError(f'New task keys must be at most {NEW_KEY_MAX} characters (reuse an existing task_id to keep a task): '
                            + ', '.join(long_keys), 'invalid')
        ids = {t.key: t.key if t.key in existing else f'{prefix}-{t.key}' for t in args.tasks}
        # A new task's generated ID must not equal another task's ID, including an existing task left out of this plan.
        if len(set(ids.values())) != len(ids) or any(k not in existing and v in existing for k, v in ids.items()):
            raise ToolError('Task keys produce duplicate task IDs; choose different keys', 'invalid')
        tasks = []
        for t in args.tasks:
            if any(d not in ids for d in t.depends_on): raise ToolError(f'Task {t.key} depends on an unknown key')
            old = existing.get(t.key, {})
            task = {'id': ids[t.key], 'title': t.title, 'acceptance_criteria': t.acceptance_criteria,
                    'parent_id': None, 'dependencies': [ids[d] for d in t.depends_on],
                    'status': old.get('status', 'pending'), 'explanation': old.get('explanation'),
                    'weight': old.get('weight', 1), 'links': old.get('links') or {'notebook_id': ctx.notebook_id},
                    'evidence': old.get('evidence', []), 'accepted': old.get('accepted', False)}
            m.Task.model_validate(task)
            tasks.append(task)
        return {'project_id': project, 'notebook_id': ctx.notebook_id,
                'roadmap_id': current['id'] if current else None,
                'roadmap_revision': current['revision'] if current else None,
                'objective': args.objective, 'tasks': tasks,
                'removed': [t for t in existing if t not in ids.values()]}
    return await run_sync(work)


def preview_roadmap(_ctx, args, binding):
    return {'title': 'Revise roadmap' if binding['roadmap_id'] else 'Create roadmap', 'objective': binding['objective'],
            'tasks': [{'title': t['title'], 'depends_on': [next(x['title'] for x in binding['tasks'] if x['id'] == d)
                                                          for d in t['dependencies']],
                       'acceptance_criteria': t['acceptance_criteria'], 'status': t['status']} for t in binding['tasks']],
            'removed_task_ids': binding['removed'], 'rationale': args.rationale,
            'roadmap_revision': binding['roadmap_revision']}


async def propose_roadmap(ctx, _args, binding):
    app = ctx.services.app

    def work():
        if binding['roadmap_id'] is None:
            return app.roadmap_create(m.RoadmapCreate(operation_id=ctx.operation_id, project_id=binding['project_id'],
                notebook_id=binding['notebook_id'], objective=binding['objective'],
                tasks=[m.Task.model_validate(t) for t in binding['tasks']]))
        return app.roadmap_update(binding['roadmap_id'], m.RoadmapUpdate(operation_id=ctx.operation_id,
            revision=binding['roadmap_revision'], objective=binding['objective'],
            tasks=[m.Task.model_validate(t) for t in binding['tasks']]))
    try:
        record = await run_sync(work)
    except NotebookError as exc:  # ApplicationError subclasses NotebookError
        if exc.status_code == 409:
            raise ToolError('The roadmap changed after this plan was proposed; re-read it and propose again', 'conflict') from None
        raise ToolError(f'Roadmap was not saved: {exc}') from None
    return {'summary': f"Saved roadmap revision {record['revision']} with {len(record['data']['tasks'])} tasks",
            'data': roadmap_view(record), 'links': {'roadmap_id': record['id']}}


async def reconcile_roadmap(ctx, args, binding, _partial):
    # The application operation journal replays a committed response exactly;
    # if nothing committed, the revision check still guards the retry.
    try:
        return 'succeeded', await propose_roadmap(ctx, args, binding)
    except ToolError as exc:
        return 'failed', {'summary': str(exc), 'status': 'conflict'}


class DelegateArgs(Args):
    task_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    instructions: str = Field(min_length=1, max_length=4000)


async def delegate_task(ctx, args, _binding):
    runtime = ctx.services.runtime
    child = await runtime.delegate(ctx.run, args.task_id, args.instructions, operation_id=ctx.operation_id)
    return {'summary': f'Assigned task {args.task_id} to the Coding Agent', 'data': {'child_run_id': child['id'], 'task_id': args.task_id}}


# --------------------------------------------------------------------------- Coding Agent

class InsertCellArgs(Args):
    source: str = Field(min_length=1, max_length=20000)
    explanation: str = Field(min_length=1, max_length=2000)
    after_cell_id: str | None = Field(default=None, pattern=r'^[a-zA-Z0-9_-]{1,64}$', description='Insert after this cell; omit to append')


def _marker(ctx):
    return {'europa_agent': {'tool_call_id': ctx.call_id, 'run_id': ctx.run['id']}}


def _bind_insert(kind):
    async def bind(ctx, args):
        document = await run_sync(ctx.services.notebooks.open, ctx.notebook_id)
        cells = document['cells']
        index = len(cells)
        if args.after_cell_id:
            positions = [i for i, c in enumerate(cells) if c['id'] == args.after_cell_id]
            if not positions: raise ToolError('after_cell_id does not exist', 'conflict')
            index = positions[0] + 1
        return {'notebook_id': ctx.notebook_id, 'revision': document['metadata']['europa']['revision'],
                'index': index, 'cell_type': kind, 'source_hash': hashlib.sha256(args.source.encode()).hexdigest()}
    return bind


def preview_insert(_ctx, args, binding):
    return {'title': f"Insert {binding['cell_type']} cell", 'source': args.source, 'explanation': args.explanation,
            'position': binding['index'] + 1, 'notebook_revision': binding['revision']}


async def insert_cell(ctx, args, binding):
    try:
        document = await run_sync(ctx.services.notebooks.edit_cell, ctx.notebook_id, binding['revision'], 'insert',
                                  cell_type=binding['cell_type'], text=args.source, metadata=_marker(ctx), index=binding['index'])
    except NotebookError as exc:
        if exc.status_code == 409:
            raise ToolError('The notebook changed after this edit was proposed (stale revision). Nothing was inserted; re-read and propose again.', 'conflict') from None
        raise ToolError(f'Insert failed: {exc}') from None
    cell = next(c for c in document['cells'] if c['metadata'].get('europa_agent', {}).get('tool_call_id') == ctx.call_id)
    return {'summary': f"Inserted {binding['cell_type']} cell at position {document['cells'].index(cell) + 1}",
            'data': {'cell_id': cell['id'], 'revision': document['metadata']['europa']['revision']},
            'links': {'cell_id': cell['id'], 'document_revision': document['metadata']['europa']['revision']}}


async def reconcile_insert(ctx, _args, binding, _partial):
    document = await run_sync(ctx.services.notebooks.open, ctx.notebook_id)
    for cell in document['cells']:
        if cell['metadata'].get('europa_agent', {}).get('tool_call_id') == ctx.call_id:
            revision = document['metadata']['europa']['revision']
            return 'succeeded', {'summary': 'Inserted cell (verified after restart)',
                                 'data': {'cell_id': cell['id'], 'revision': revision},
                                 'links': {'cell_id': cell['id']}}
    # The marker is written atomically with the cell: absent means not applied.
    return 'not_applied', None


class ReplaceCellArgs(Args):
    cell_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    source: str = Field(min_length=1, max_length=20000)
    explanation: str = Field(min_length=1, max_length=2000)


async def bind_replace(ctx, args):
    document = await run_sync(ctx.services.notebooks.open, ctx.notebook_id)
    try: cell = ctx.services.notebooks.cell(document, args.cell_id)
    except NotebookError: raise ToolError('Cell does not exist', 'conflict') from None
    return {'notebook_id': ctx.notebook_id, 'revision': document['metadata']['europa']['revision'],
            'cell_id': args.cell_id, 'before_hash': source_hash(cell), 'before': clip(source(cell), 20000),
            'after_hash': hashlib.sha256(args.source.encode()).hexdigest()}


def preview_replace(_ctx, args, binding):
    return {'title': 'Replace cell source', 'cell_id': binding['cell_id'], 'before': binding['before'],
            'source': args.source, 'explanation': args.explanation, 'notebook_revision': binding['revision']}


async def replace_cell(ctx, args, binding):
    def work():
        document = ctx.services.notebooks.open(ctx.notebook_id)
        cell = ctx.services.notebooks.cell(document, binding['cell_id'])
        if source_hash(cell) != binding['before_hash']:
            raise ToolError('The cell was edited after this change was proposed. It was not overwritten.', 'conflict')
        return ctx.services.notebooks.edit_cell(ctx.notebook_id, binding['revision'], 'edit', binding['cell_id'], text=args.source)
    try:
        document = await run_sync(work)
    except NotebookError as exc:
        if exc.status_code in {404, 409}:
            raise ToolError('The notebook changed after this edit was proposed. Nothing was overwritten; re-read and propose again.', 'conflict') from None
        raise ToolError(f'Replace failed: {exc}') from None
    revision = document['metadata']['europa']['revision']
    return {'summary': 'Replaced cell source', 'data': {'cell_id': binding['cell_id'], 'revision': revision},
            'links': {'cell_id': binding['cell_id'], 'document_revision': revision}}


async def reconcile_replace(ctx, _args, binding, _partial):
    document = await run_sync(ctx.services.notebooks.open, ctx.notebook_id)
    try: cell = ctx.services.notebooks.cell(document, binding['cell_id'])
    except NotebookError: return 'uncertain', {'summary': 'Target cell disappeared; outcome cannot be verified'}
    current = source_hash(cell)
    if current == binding['after_hash']:
        return 'succeeded', {'summary': 'Replaced cell source (verified after restart)', 'data': {'cell_id': binding['cell_id']}}
    if current == binding['before_hash'] and document['metadata']['europa']['revision'] == binding['revision']:
        return 'not_applied', None
    return 'uncertain', {'summary': 'Cell changed to a third state; review before continuing'}


class ExecuteCellArgs(Args):
    cell_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    explanation: str = Field(default='', max_length=2000)


async def bind_execute(ctx, args):
    document = await run_sync(ctx.services.notebooks.open, ctx.notebook_id)
    try: cell = ctx.services.notebooks.cell(document, args.cell_id)
    except NotebookError: raise ToolError('Cell does not exist', 'conflict') from None
    if cell['cell_type'] != 'code' or not source(cell).strip():
        raise ToolError('Only nonblank code cells can be executed')
    return {'notebook_id': ctx.notebook_id, 'revision': document['metadata']['europa']['revision'],
            'cell_id': args.cell_id, 'source_hash': source_hash(cell), 'source': clip(source(cell), 20000)}


def preview_execute(_ctx, args, binding):
    return {'title': 'Run code cell', 'cell_id': binding['cell_id'], 'source': binding['source'],
            'explanation': args.explanation, 'notebook_revision': binding['revision'],
            'warning': 'Runs in this notebook kernel with your local permissions. Not a sandbox.'}


async def _await_receipt(ctx, execution_id, deadline):
    while True:
        receipt = await run_sync(ctx.services.notebooks.execution, ctx.notebook_id, execution_id)
        if receipt['state'] in TERMINAL_RECEIPTS or time.monotonic() > deadline:
            return receipt
        await asyncio.sleep(0.25)


def _execution_result(receipt):
    view = receipt_view(receipt)
    settled = receipt['state'] in TERMINAL_RECEIPTS
    if not settled:
        summary = f"Execution {view['execution_id'][:8]} still running; outcome not yet known"
    elif receipt['state'] == 'applied':
        summary = f"Execution {view['execution_id'][:8]} finished: {view['status']}"
    else:
        summary = f"Execution {view['execution_id'][:8]} is {receipt['state']}; no output is claimed"
    return {'summary': summary, 'data': view, 'status': 'ok' if view['status'] == 'ok' and receipt['state'] == 'applied' else 'error',
            'links': {'cell_id': receipt['cell_id'], 'execution_receipt_id': receipt['execution_id']}}


async def execute_cell(ctx, args, binding):
    def check():
        document = ctx.services.notebooks.open(ctx.notebook_id)
        cell = ctx.services.notebooks.cell(document, binding['cell_id'])
        if document['metadata']['europa']['revision'] != binding['revision'] or source_hash(cell) != binding['source_hash']:
            raise ToolError('The notebook or cell changed after this run was proposed; it was not executed.', 'conflict')
    try:
        await run_sync(check)
    except NotebookError:
        raise ToolError('The target cell no longer exists; it was not executed.', 'conflict') from None
    service = ctx.services.notebook_service()
    if service is None: raise ToolError('Notebook execution service is unavailable')
    try:
        receipt = await service.submit(ctx.notebook_id, binding['cell_id'], binding['revision'], timeout=120)
    except NotebookError as exc:
        kind = 'conflict' if exc.status_code == 409 else 'error'
        raise ToolError(f'Execution was not accepted: {exc}', kind) from None
    # Persist the receipt identity before waiting: it is the reconciliation anchor.
    await ctx.services.runtime.record_progress(ctx, {'execution_id': receipt['execution_id']})
    final = await _await_receipt(ctx, receipt['execution_id'], time.monotonic() + 150)
    return _execution_result(final)


async def reconcile_execute(ctx, _args, binding, partial):
    execution = (partial or {}).get('execution_id')
    if execution is None:
        receipts = await run_sync(ctx.services.notebooks.executions, ctx.notebook_id)
        started = (partial or {}).get('started_at', 0)
        matching = [r for r in receipts if r['cell_id'] == binding['cell_id'] and r['created_at'] >= started
                    and r['source_hash'] == binding['source_hash']]
        if not matching:
            return 'not_applied', None  # Receipts are written before any kernel request.
        if len(matching) > 1:
            return 'uncertain', {'summary': 'Several executions match; outcome must be reviewed'}
        execution = matching[0]['execution_id']
    receipt = await run_sync(ctx.services.notebooks.execution, ctx.notebook_id, execution)
    if receipt['state'] not in TERMINAL_RECEIPTS and receipt['state'] != 'completed':
        return 'uncertain', {'summary': f'Execution {execution[:8]} outcome is unknown; it was not re-run',
                             'links': {'execution_receipt_id': execution}}
    return 'succeeded', _execution_result(receipt)


# --------------------------------------------------------------------------- registry

def _tool(name, description, args, capability, execute, *, approval=False, idempotency='read', timeout=30.0,
          bind=None, preview=None, reconcile=None, version=1):
    return Tool(name=name, version=version, description=description, args=args, capability=capability,
                approval=approval, idempotency=idempotency, timeout=timeout, execute=execute, bind=bind,
                preview=preview, reconcile=reconcile)


REGISTRY = {tool.name: tool for tool in [
    _tool('list_workspace', 'List verified entries in a workspace directory.', WorkspaceListArgs, 'read:workspace', list_workspace),
    _tool('list_projects', 'List Europa projects with notebook counts.', NoArgs, 'read:workspace', list_projects),
    _tool('list_notebooks', 'List notebooks known to Europa.', NoArgs, 'read:workspace', list_notebooks),
    _tool('read_notebook_summary', 'Read a bounded summary of any notebook by ID.', ReadNotebookArgs, 'read:workspace', read_any_notebook),
    _tool('create_project_notebook', 'Propose creating a project folder (optional) and a new notebook. Requires owner approval.',
          CreateProjectNotebookArgs, 'propose:workspace', create_project_notebook, approval=True, idempotency='operation_id',
          timeout=60, bind=bind_create, preview=preview_create, reconcile=reconcile_create),
    _tool('read_notebook', "Read this notebook's cells, outputs and revision.", NoArgs, 'read:notebook', read_own_notebook),
    _tool('read_roadmap', "Read this notebook's project roadmap and task states.", NoArgs, 'read:roadmap', read_roadmap),
    _tool('read_execution', 'Read an execution receipt of this notebook.', ReadExecutionArgs, 'read:notebook', read_execution),
    _tool('propose_roadmap', 'Propose a new or revised roadmap for this notebook. Requires owner approval.',
          ProposeRoadmapArgs, 'propose:roadmap', propose_roadmap, approval=True, idempotency='operation_id',
          bind=bind_roadmap, preview=preview_roadmap, reconcile=reconcile_roadmap),
    _tool('delegate_task', 'Assign one approved roadmap task whose dependencies are complete to the Coding Agent.',
          DelegateArgs, 'delegate', delegate_task, idempotency='operation_id'),
    _tool('insert_code_cell', 'Propose inserting a code cell. Requires owner approval. Does not run it.',
          InsertCellArgs, 'edit:notebook', insert_cell, approval=True, idempotency='marker',
          bind=_bind_insert('code'), preview=preview_insert, reconcile=reconcile_insert),
    _tool('insert_markdown_cell', 'Propose inserting a Markdown cell. Requires owner approval.',
          InsertCellArgs, 'edit:notebook', insert_cell, approval=True, idempotency='marker',
          bind=_bind_insert('markdown'), preview=preview_insert, reconcile=reconcile_insert),
    _tool('replace_cell_source', 'Propose replacing the source of one cell. Requires owner approval.',
          ReplaceCellArgs, 'edit:notebook', replace_cell, approval=True, idempotency='content_hash',
          bind=bind_replace, preview=preview_replace, reconcile=reconcile_replace),
    _tool('execute_cell', 'Propose running one saved code cell through the notebook kernel. Requires owner approval.',
          ExecuteCellArgs, 'execute:notebook', execute_cell, approval=True, idempotency='receipt', timeout=180,
          bind=bind_execute, preview=preview_execute, reconcile=reconcile_execute),
]}


def digest(tool, arguments, binding, run_id):
    canonical = json.dumps({'tool': tool.name, 'version': tool.version, 'arguments': arguments,
                            'binding': binding, 'run_id': run_id}, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()
