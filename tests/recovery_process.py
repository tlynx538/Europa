"""Disposable control plane for opt-in SIGKILL tests; never prints credentials."""
import asyncio
import json
from pathlib import Path
import sys

from main import create_app
from runtime.containerd import ContainerdRuntimeProvider
from runtime.containerd_backend import SDKBridgeBackend


async def run(path, mode):
    app = create_app()
    async def checkpoint(extra=None):
        records = app.state.supervisor.registry.records()
        data = {'records': [{k: row[k] for k in ('runtime_id', 'notebook_id', 'generation', 'phase')} for row in records]}
        data.update(extra or {})
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data))
        temporary.replace(path)
        await asyncio.Event().wait()

    original_request = SDKBridgeBackend.request
    async def request(self, operation, identity, **parameters):
        if mode == 'deleting' and operation == 'destroy':
            await checkpoint()
        result = await original_request(self, operation, identity, **parameters)
        if (mode == 'created' and operation == 'create') or (mode == 'deleted' and operation == 'destroy'):
            await checkpoint()
        return result
    SDKBridgeBackend.request = request
    if mode == 'started':
        original_ready = ContainerdRuntimeProvider._ready
        async def ready(self, record):
            await original_ready(self, record)
            await checkpoint()
        ContainerdRuntimeProvider._ready = ready
    async with app.router.lifespan_context(app):
        supervisor = app.state.supervisor
        if mode in {'deleting', 'deleted'}:
            await supervisor.destroy_notebook('default')
        kernels = {}
        for name in ['default', 'other'] if mode == 'serve-two' else ['default']:
            client = await supervisor.client_for(name)
            kernel = (await client.create_kernel())['id']
            result = await client.execute(kernel, f"owner_value={name!r}; print(owner_value)")
            assert result['status'] == 'ok'
            kernels[name] = kernel
        await checkpoint({'kernels': kernels})


if __name__ == '__main__':
    asyncio.run(run(Path(sys.argv[1]), sys.argv[2]))
