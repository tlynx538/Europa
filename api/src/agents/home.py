"""Home coordinator effects, using the existing workspace journals and bindings."""
from pydantic import Field, field_validator
from agents.models import Identity
from agents.store import AgentFault
from agents.tools import Directory, Tool, ToolResult
from application.store import ApplicationError
from notebooks.store import NotebookError
from workspace.browser import WorkspaceError, _segments
from workspace.service import associate_notebook


class CreateWorkspaceNotebook(Directory):
    notebook_name: str = Field(min_length=7, max_length=128)
    objective: str = Field(min_length=1, max_length=1200)
    project_id: Identity | None = None

    @field_validator('notebook_name')
    @classmethod
    def filename(cls, value):
        try:
            if len(_segments(value)) != 1 or not value.lower().endswith('.ipynb'):
                raise ValueError('A plain .ipynb filename is required')
        except WorkspaceError as exc: raise ValueError('A plain .ipynb filename is required') from exc
        return value


class UncertainEffect(Exception):
    """A side effect may have happened; do not repeat it automatically."""


class PartialEffect(Exception):
    def __init__(self, reason, result):
        super().__init__(reason)
        self.result = result


class HomeTools:
    def __init__(self, store, state): self.store, self.state = store, state

    def declaration(self):
        return Tool('create_workspace_notebook',
                    'Propose an exact physical project directory and .ipynb file for owner approval. Use project_id only for an existing verified project.',
                    CreateWorkspaceNotebook, self.create, 'workspace_resources', capability='workspace.create',
                    classification='write', approval=True, timeout=20, idempotency='workspace-journal',
                    roles=frozenset({'coordinator'}))

    def create(self, run, args, operation):
        if run['scope']['kind'] != 'workspace': raise AgentFault('Workspace creation is outside this scope',403)
        path = args.path + '/' + args.notebook_name
        project_op, notebook_op = operation+'-project', operation+'-notebook'
        try:
            if args.project_id:
                project = self.state.workspace.project(args.path)
                if project['id'] != args.project_id: raise AgentFault('Approved project binding changed',409)
            else:
                project = self.state.workspace_mutation.create_project({'path':args.path,'operation_id':project_op})
        except (ApplicationError, NotebookError, WorkspaceError) as exc:
            with self.store.app.mutex:
                began = self.store.db.execute('SELECT 1 FROM project_intents WHERE operation_id=?',(project_op,)).fetchone()
            if began: raise UncertainEffect() from exc
            raise
        try:
            with self.store.app.notebooks.locked(), self.store.app.mutex:
                # The physical binding can precede its first catalog record.
                if self.store.db.execute('SELECT 1 FROM records WHERE id=?', (project['id'],)).fetchone():
                    self.store.app._project(project['id'])
                created = associate_notebook(self.state,path,notebook_op,create=True,expected_project_id=project['id'])
        except (ApplicationError, NotebookError, WorkspaceError) as exc:
            with self.store.app.notebooks.locked():
                began=any(row['operation_id']==notebook_op for row in self.store.app.notebooks._manifest().values())
            if began or exc.status_code>=500: raise UncertainEffect() from exc
            raise PartialEffect('Project is available; notebook creation was refused',
                                {'project_id':project['id'],'project_path':args.path,'notebook_created':False}) from exc
        handoff = self.store.record_handoff(run,operation,created,args.objective)
        return ToolResult(data={'notebook_id':created['notebook_id'],'project_id':created['project_id'],
                                'path':path,'handoff_id':handoff['id'],'conversation_id':handoff['destination_conversation_id']},
                          provenance={'workspace_operation':notebook_op,'handoff_id':handoff['id']})
