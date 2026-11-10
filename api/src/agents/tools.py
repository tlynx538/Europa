"""Typed tool declarations and scope policy; registered tools alone are callable."""
from dataclasses import dataclass
import json
from typing import Annotated, Callable

from pydantic import Field, StrictInt, ValidationError
from agents.models import Identity, Model
from agents.store import AgentFault
from application.store import encoded
from notebooks.store import NotebookError, source
from workspace.browser import WorkspaceError, _segments


class Empty(Model):
    pass


class NotebookTarget(Model):
    notebook_id: Identity


class Cells(NotebookTarget):
    cell_ids: list[Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')]] = Field(default_factory=list, max_length=8)
    limit: StrictInt = Field(default=4, ge=1, le=8)


class Directory(Model):
    path: str = Field(max_length=4096)


class RoadmapTarget(Model):
    roadmap_id: Identity


class ToolResult(Model):
    data: dict | list
    provenance: dict
    truncated: bool = False


def clip(text, limit):
    return text.encode()[:limit].decode('utf-8', errors='ignore')


def preview_cell(cell, budget=1200):
    outputs = []
    for output in cell.get('outputs', [])[:4]:
        plain = output.get('text', output.get('data', {}).get('text/plain', output.get('traceback', [])))
        plain = ''.join(plain) if isinstance(plain, list) and all(isinstance(p, str) for p in plain) else plain
        outputs.append({'type': output['output_type'], 'text': clip(plain, 400) if isinstance(plain, str) else '',
                        'mime_types': list(output.get('data', {}))[:8]})
    return {'id': cell['id'], 'type': cell['cell_type'], 'source': clip(source(cell), budget), 'outputs': outputs,
            'source_truncated': len(source(cell).encode()) > budget, 'outputs_bounded': True}


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    arguments: type[Model]
    handler: Callable
    target_type: str
    capability: str = 'read'
    classification: str = 'read'
    approval: bool = False
    timeout: float = 8
    idempotency: str = 'read-only'
    version: int = 1
    result: type[Model] = ToolResult
    roles: frozenset[str] = frozenset({'coordinator', 'coding_agent'})

    def definition(self):
        return {'type': 'function', 'function': {'name': self.name, 'description': self.description,
                                                'parameters': self.arguments.model_json_schema()}}


class ToolRegistry:
    def __init__(self, tools):
        self.tools = {}
        for tool in tools:
            if tool.name in self.tools or not 0 < tool.timeout <= 300 or tool.classification not in {'read','write','execute','delegate'}:
                raise ValueError('Invalid tool declaration')
            if tool.classification != 'read' and not tool.approval:
                raise ValueError('Effects require exact human approval')
            if tool.classification == 'execute' and 'coordinator' in tool.roles:
                raise ValueError('Coordinator cannot execute')
            self.tools[tool.name] = tool

    def get(self, name):
        if name not in self.tools: raise AgentFault('Model proposed an unregistered tool', 403)
        return self.tools[name]

    def available(self, run):
        return [t.definition() for t in self.tools.values() if t.capability in run['grant_json']['capabilities'] and run['role'] in t.roles]

    def validate(self, name, arguments):
        tool = self.get(name)
        if len(encoded(arguments).encode()) > 16384: raise AgentFault('Tool arguments exceed limit', 413)
        try: return tool, tool.arguments.model_validate(arguments)
        except ValidationError as exc: raise AgentFault('Tool arguments do not match the registered schema', 400) from exc


class PolicyEngine:
    def __init__(self, store): self.store = store

    def authorize(self, run, tool, args):
        grant = run['grant_json']
        if tool.capability not in grant['capabilities'] or run['role'] not in tool.roles:
            raise AgentFault('Tool capability is not granted to this agent role', 403)
        with self.store.app.notebooks.locked(), self.store.app.mutex:
            self.store.scope_current(run)
            target = {'scope': run['scope']['kind']}
            notebook = getattr(args, 'notebook_id', None)
            if notebook:
                if notebook not in grant['notebooks']: raise AgentFault('Notebook is outside this run grant', 403)
                if grant['project_id']: self.store.app._scope(grant['project_id'], notebook)
                self.store.app._notebook_scope(notebook)
                target['notebook_id'] = notebook
            if grant['project_id']: target['project_id'] = grant['project_id']
            if isinstance(args, Directory):
                root = grant['directory']
                path = _segments(args.path)
                if root is None or path[:len(_segments(root))] != _segments(root):
                    raise AgentFault('Directory is outside this run grant', 403)
                target['path'] = args.path
                if tool.target_type == 'workspace_resources':
                    if run['scope']['kind'] != 'workspace' or len(path) != len(_segments(root))+1:
                        raise AgentFault('Create only in the granted directory',403)
                    target['notebook_path'] = args.path + '/' + args.notebook_name
                    if args.project_id: target['project_id'] = args.project_id
            if isinstance(args, RoadmapTarget):
                roadmap = self.store.app._row(args.roadmap_id, 'roadmap')
                if not grant['project_id'] or roadmap['project_id'] != grant['project_id'] or (roadmap['notebook_id'] and roadmap['notebook_id'] not in grant['notebooks']):
                    raise AgentFault('Roadmap is outside this run grant', 403)
                target['roadmap_id'] = args.roadmap_id
            if tool.target_type == 'notebook_roadmap' and args.roadmap_id:
                roadmap = self.store.app._row(args.roadmap_id, 'roadmap')
                if (roadmap['project_id'], roadmap['notebook_id']) != (grant['project_id'], notebook):
                    raise AgentFault('Roadmap is outside this notebook grant', 403)
                target.update(roadmap_id=args.roadmap_id, roadmap_revision=args.roadmap_revision)
            for key in ('revision','cell_id'):
                value = getattr(args, key, None)
                if value is not None: target[key] = value
            return target


class ReadTools:
    def __init__(self, store): self.store = store

    def projects(self, run, args, operation):
        app = self.store.app
        scope = run['scope']['kind']
        with app.notebooks.locked(), app.mutex:
            if scope == 'workspace':
                rows = app.db.execute("SELECT id,revision,data FROM records WHERE kind='project' AND json_extract(data,'$.status')='active' ORDER BY sequence DESC LIMIT 21").fetchall()
            elif run['grant_json']['project_id']:
                rows = [app._project(run['grant_json']['project_id'])]
            else: rows = []
            items = []
            for row in rows[:20]:
                binding = app.db.execute('SELECT path,root_device,root_inode FROM project_bindings WHERE project_id=?', (row['id'],)).fetchone()
                path = None
                if binding:
                    if [binding['root_device'],binding['root_inode']] != self.store.root: continue
                    try:
                        if self.store.workspace.project(binding['path'])['id'] != row['id']: continue
                    except WorkspaceError: continue
                    path = binding['path']
                items.append({'id': row['id'], 'title': clip(json.loads(row['data'])['title'], 200), 'revision': row['revision'],
                              'path': path, 'location': 'verified_directory' if path else 'catalog'})
            return ToolResult(data=items, provenance={'resource': 'projects'}, truncated=len(rows)>20)

    def notebooks(self, run, args, operation):
        result = []
        app = self.store.app
        with app.notebooks.locked(), app.mutex:
            for notebook in run['grant_json']['notebooks']:
                try:
                    if run['grant_json']['project_id']: app._scope(run['grant_json']['project_id'], notebook)
                    document = app.notebooks.open(notebook)
                    meta = document['metadata']['europa']
                    result.append({'notebook_id': notebook, 'title': meta['title'], 'revision': meta['revision'], 'cell_count': len(document['cells'])})
                except NotebookError: continue
        return ToolResult(data=result, provenance={'resource': 'notebook_grant'})

    def metadata(self, run, args, operation):
        doc = self.store.app.notebooks.open(args.notebook_id)
        meta = doc['metadata']['europa']
        return ToolResult(data={'title': meta['title'], 'cell_count': len(doc['cells'])},
                          provenance={'notebook_id': args.notebook_id, 'revision': meta['revision']})

    def cells(self, run, args, operation):
        doc = self.store.app.notebooks.open(args.notebook_id)
        selected = [self.store.app.notebooks.cell(doc, identity) for identity in args.cell_ids] if args.cell_ids else doc['cells']
        return ToolResult(data=[preview_cell(c, 800) for c in selected[:args.limit]],
                          provenance={'notebook_id': args.notebook_id, 'revision': doc['metadata']['europa']['revision']},
                          truncated=len(selected)>args.limit)

    def roadmap(self, run, args, operation):
        with self.store.app.mutex:
            row = self.store.app._row(args.roadmap_id, 'roadmap')
            data = json.loads(row['data'])
        return ToolResult(data={'objective': clip(data['objective'], 800), 'tasks': [
            {'id': t['id'], 'title': t['title'], 'status': t['status'], 'dependencies': t['dependencies'][:12]}
            for t in data['tasks'][:12]]}, provenance={'roadmap_id': row['id'], 'revision': row['revision']}, truncated=len(data['tasks'])>12)

    def directory(self, run, args, operation):
        page = self.store.workspace.list(args.path, None, 20)
        return ToolResult(data=page['items'], provenance={'directory': page['path']}, truncated=page['next_cursor'] is not None)

    def declarations(self):
        return [Tool('list_projects', 'List verified projects visible to this scope.', Empty, self.projects, 'project'),
                Tool('list_notebooks', 'List notebooks in the explicit read grant.', Empty, self.notebooks, 'notebook'),
                Tool('read_notebook_metadata', 'Read an authorized notebook title and revision.', NotebookTarget, self.metadata, 'notebook'),
                Tool('read_notebook_cells', 'Read bounded authorized cells and text outputs.', Cells, self.cells, 'notebook'),
                Tool('read_roadmap', 'Read a roadmap within the verified project grant.', RoadmapTarget, self.roadmap, 'roadmap'),
                Tool('list_workspace', 'List directory entries under the granted workspace directory.', Directory, self.directory, 'directory')]
