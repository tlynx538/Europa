"""Shared fixtures for agent harness tests: real stores, scripted fake provider."""
import asyncio
import json
import uuid

import httpx

from agents.providers import FakeProvider, ModelResponse, ToolCallRequest
from agents.runtime import AgentRuntime, Services
from application import models as m
from application.store import ApplicationStore
from main import create_app
from workspace.mutation import WorkspaceMutation
from workspace.notebooks import WorkspaceNotebookStore


def op():
    return uuid.uuid4().hex


def reply(text, usage=None):
    return ModelResponse(content=text, usage=usage or {})


def call(name, note='', **arguments):
    return ModelResponse(content=note, tool_calls=[ToolCallRequest(id='c' + uuid.uuid4().hex[:8], name=name, arguments=json.dumps(arguments))])


def calls(*items, note=''):
    return ModelResponse(content=note, tool_calls=[ToolCallRequest(id='c' + uuid.uuid4().hex[:8], name=n, arguments=json.dumps(a)) for n, a in items])


class Harness:
    def __init__(self, tmp_path, script, *, notebook_service=None, runtime_options=None):
        self.tmp_path = tmp_path
        self.root = tmp_path / 'workspace'
        self.root.mkdir(exist_ok=True)
        self.app = create_app(workspace_root=self.root)
        self.notebooks = WorkspaceNotebookStore(tmp_path / 'private', self.app.state.workspace)
        self.application = ApplicationStore(tmp_path / 'application', self.notebooks)
        self.mutation = WorkspaceMutation(self.app.state.workspace, self.application)
        self.app.state.notebook_store = self.notebooks
        self.app.state.application_store = self.application
        self.app.state.workspace_mutation = self.mutation
        self.app.state.notebook_service = notebook_service
        self.provider = script if isinstance(script, FakeProvider) else FakeProvider(script)
        self.runtime_options = runtime_options or {}
        self.runtime = None

    async def start(self):
        services = Services(app=self.application, notebooks=self.notebooks, workspace=self.app.state.workspace,
                            mutation=self.mutation, notebook_service=lambda: self.app.state.notebook_service)
        self.runtime = AgentRuntime(services, self.provider, poll_interval=0.02, **self.runtime_options)
        self.app.state.agent_runtime = self.runtime
        await self.runtime.start()
        return self

    async def restart(self, script=None):
        """Simulate a control-plane restart over the same durable storage."""
        await self.stop()
        if script is not None:
            self.provider = script if isinstance(script, FakeProvider) else FakeProvider(script)
        await self.start()

    async def stop(self):
        if self.runtime:
            await self.runtime.close(grace=0.01)
            self.runtime = None

    def close(self):
        self.application.close(); self.notebooks.close(); self.app.state.workspace.close()

    async def http(self, method, path, **kwargs):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://europa') as client:
            return await getattr(client, method)('/api/' + path, **kwargs)

    # ------------------------------------------------------------- data helpers
    def project_notebook(self, name='Analysis', notebook='work.ipynb', cells=('print(1)',)):
        self.mutation.create_project({'operation_id': op(), 'path': name})
        nid = self.notebooks.associate(f'{name}/{notebook}', op(), create=True)
        location = self.notebooks.location(nid)
        conversation = self.application.bind_physical_notebook(nid, location)
        revision = 1
        for text in cells:
            revision = self.notebooks.edit_cell(nid, revision, 'insert', text=text)['metadata']['europa']['revision']
        return nid, location['project_id'], conversation['id']

    def standalone_notebook(self, title='Loose'):
        nid = self.notebooks.create(title)['metadata']['europa']['notebook_id']
        return nid, self.application.ensure_notebook_conversation(nid)['id']

    def say(self, conversation, content, **links):
        return self.application.message_append(conversation, m.MessageCreate(operation_id=op(), content=content, **links))

    async def ask(self, conversation, content, **links):
        message = await asyncio.to_thread(self.say, conversation, content, **links)
        return await self.runtime.start_run(operation_id=op(), conversation_id=conversation, message_id=message['id'])

    async def settle(self, run_id, states=('completed', 'failed', 'cancelled', 'interrupted', 'waiting_for_approval'), timeout=20):
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            run = await asyncio.to_thread(self.runtime.store.get_run, run_id)
            if run['state'] in states: return run
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(f'run stuck in {run["state"]}')
            await asyncio.sleep(0.02)

    def events(self, run_id):
        return [e['type'] for e in self.runtime.store.events(run_id, 0, 200)['items']]

    def pending_approval(self, run_id):
        return next(a for a in self.runtime.store.approvals(run_id) if a['state'] == 'pending')

    async def approve(self, run_id, decision='approve', operation=None):
        approval = self.pending_approval(run_id)
        await self.runtime.decide(approval['id'], operation_id=operation or op(), decision=decision, digest_value=approval['digest'])
        return approval

    def messages(self, conversation):
        return self.application.listing('message', parent=conversation, limit=100)['items']
