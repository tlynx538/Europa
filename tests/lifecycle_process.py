"""Disposable production control plane with synchronized SIGKILL checkpoints."""
import asyncio
import json
from pathlib import Path
import sys
import time

from main import create_app
from runtime.lifecycle import IdlePolicy


async def run(path, mode):
    app = create_app()
    async with app.router.lifespan_context(app):
        s = app.state.supervisor
        life = s.lifecycle
        async def checkpoint():
            rows = [{k: r[k] for k in ('runtime_id', 'notebook_id', 'generation', 'phase')} for r in s.registry.records()]
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'records': rows}))
            temporary.replace(path)
            await asyncio.Event().wait()
        if mode == 'execution':
            client = await s.client_for('default')
            kernel = (await client.create_kernel())['id']
            raw = client.client
            async with raw._session_ready().ws_connect(raw._websocket_url(kernel, 'checkpoint')) as observer:
                await raw._handshake(observer, 'checkpoint')
                asyncio.create_task(client.execute(kernel, 'import threading; threading.Event().wait(120)', timeout=150))
                async with asyncio.timeout(20):
                    while (await raw._receive(observer))['header'].get('msg_type') != 'execute_input':
                        pass
                await checkpoint()
        else:
            backend = s.providers['containerd'].backend
            request = backend.request
            async def paused(operation, identity, **kwargs):
                if mode == 'deleting' and operation == 'destroy': await checkpoint()
                result = await request(operation, identity, **kwargs)
                if mode == 'deleted' and operation == 'destroy': await checkpoint()
                return result
            backend.request = paused
            life.policy = IdlePolicy(True, .01, 0, 30)
            life.clock = lambda: time.time() + 100
            await life.sweep()
            raise AssertionError('Cleanup did not reach checkpoint')


if __name__ == '__main__':
    asyncio.run(run(Path(sys.argv[1]), sys.argv[2]))
