"""Owner-reviewed, revision-bound updates to the existing notebook roadmap."""
import json
from typing import Annotated

from pydantic import Field, StrictInt, model_validator

from agents.models import Identity, Model
from agents.store import AgentFault
from agents.tools import NotebookTarget, Tool, ToolResult
from application.models import Links, RoadmapCreate, RoadmapUpdate, Task

TaskID = Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')]
Criterion = Annotated[str, Field(min_length=1, max_length=500)]


class PlanTask(Model):
    id: TaskID
    title: str = Field(min_length=1, max_length=180)
    acceptance_criteria: list[Criterion] = Field(min_length=1, max_length=4)
    dependencies: list[TaskID] = Field(default_factory=list, max_length=12)
    cell_id: TaskID | None = None


class NotebookPlan(NotebookTarget):
    revision: StrictInt = Field(ge=1)
    roadmap_id: Identity | None = None
    roadmap_revision: StrictInt | None = Field(default=None, ge=1)
    objective: str = Field(min_length=1, max_length=1200)
    tasks: list[PlanTask] = Field(min_length=1, max_length=12)

    @model_validator(mode='after')
    def validate_graph(self):
        if (self.roadmap_id is None) != (self.roadmap_revision is None):
            raise ValueError('An existing roadmap requires an expected revision')
        tasks = {task.id: task for task in self.tasks}
        if len(tasks) != len(self.tasks) or not self.objective.strip():
            raise ValueError('Use unique task IDs and a nonblank objective')
        resolved = set()
        for task in self.tasks:
            if not task.title.strip() or any(not criterion.strip() for criterion in task.acceptance_criteria):
                raise ValueError('Task titles and criteria must be nonblank')
            if not set(task.dependencies) <= resolved or len(task.dependencies) != len(set(task.dependencies)):
                raise ValueError('List each dependency once, before its dependent task')
            resolved.add(task.id)
        return self


class NotebookPlanning:
    def __init__(self, store):
        self.store = store

    def declaration(self):
        return Tool('save_notebook_plan',
                    'Propose a roadmap for the authorized notebook. Use its current revision. For updates provide the current roadmap ID and revision, and list prerequisites before dependent tasks.',
                    NotebookPlan, self.save, 'notebook_roadmap', capability='roadmap.plan',
                    classification='write', approval=True, idempotency='application-operation',
                    roles=frozenset({'coordinator'}))

    def save(self, run, args, operation):
        app = self.store.app
        project = run['grant_json']['project_id']
        with app.notebooks.locked(), app.mutex:
            self.store._fence(run['id'], run['owner'], run['generation'], cancelling=True)
            document = self.store.scope_current(run)
            if not project or run['scope']['resource_id'] != args.notebook_id:
                raise AgentFault('Roadmap is outside the notebook grant', 403)
            if document['metadata']['europa']['revision'] != args.revision:
                raise AgentFault('Notebook revision changed after the plan was proposed', 409)
            existing = {}
            if args.roadmap_id:
                row = app._row(args.roadmap_id, 'roadmap')
                if (row['project_id'], row['notebook_id']) != (project, args.notebook_id):
                    raise AgentFault('Roadmap is outside the notebook grant', 403)
                if row['revision'] != args.roadmap_revision:
                    raise AgentFault('Roadmap revision changed after the plan was proposed', 409)
                existing = {task['id']: Task.model_validate({k:v for k,v in task.items() if k!='accepted_by'})
                            for task in json.loads(row['data'])['tasks']}
            elif app.db.execute("SELECT 1 FROM records WHERE kind='roadmap' AND notebook_id=? AND project_id=? LIMIT 1",
                                (args.notebook_id, project)).fetchone():
                raise AgentFault('A notebook roadmap already exists', 409)
            tasks = []
            for item in args.tasks:
                prior = existing.pop(item.id, None)
                fields = item.model_dump(exclude={'cell_id'})
                if prior:
                    changed = (prior.title != item.title or prior.acceptance_criteria != item.acceptance_criteria
                               or prior.dependencies != item.dependencies or prior.links.cell_id != item.cell_id)
                    if changed and (prior.status != 'pending' or prior.evidence or prior.accepted):
                        raise AgentFault('The proposed plan changes a task with recorded progress', 409)
                    links = prior.links
                    if prior.links.cell_id != item.cell_id:
                        links = Links(notebook_id=args.notebook_id, cell_id=item.cell_id,
                                      document_revision=args.revision if item.cell_id else None)
                    task = prior.model_copy(update={**fields, 'links':links})
                else:
                    task = Task(**fields, links=Links(notebook_id=args.notebook_id, cell_id=item.cell_id,
                                                     document_revision=args.revision if item.cell_id else None))
                tasks.append(task)
            if any(t.status != 'pending' or t.evidence or t.accepted for t in existing.values()):
                raise AgentFault('The proposed plan removes a task with recorded progress', 409)
            fields = {'operation_id':operation,'objective':args.objective,'tasks':tasks}
            if args.roadmap_id:
                record = app.roadmap_update(args.roadmap_id, RoadmapUpdate(revision=args.roadmap_revision, **fields))
            else:
                record = app.roadmap_create(RoadmapCreate(project_id=project, notebook_id=args.notebook_id, **fields))
        return ToolResult(data={'roadmap_id':record['id'],'revision':record['revision'],'task_count':len(tasks)},
                          provenance={'notebook_id':args.notebook_id,'notebook_revision':args.revision,'project_id':project})
