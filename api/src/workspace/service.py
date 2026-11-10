"""Shared verified notebook association for REST and approved harness actions."""
from workspace.browser import WorkspaceError


def associate_notebook(state, path, operation_id, *, create=False, expected_project_id=None):
    store = state.notebook_store
    notebook = store.associate(path, operation_id, create=create, expected_project_id=expected_project_id)
    location = store.location(notebook)
    if expected_project_id and location.get('project_id') != expected_project_id:
        raise WorkspaceError(409, 'Approved project binding changed')
    if location.get('project_id'):
        project = state.workspace.project(location['project_path'])
        if project['id'] != location['project_id']:
            raise WorkspaceError(409, 'Project binding changed')
    state.application_store.bind_physical_notebook(notebook, location)
    return {'notebook_id': notebook, **location}
