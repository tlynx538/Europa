"""Disposable production app for durable notebook SIGKILL checkpoints."""
import asyncio
import json
from pathlib import Path
import sys

from main import create_app


async def run(path, boundary):
    app = create_app()
    async with app.router.lifespan_context(app):
        s, store, service = app.state.supervisor, app.state.notebook_store, app.state.notebook_service
        doc = store.create('Crash recovery')
        notebook = doc['metadata']['europa']['notebook_id']
        code = 'saved_value=42; print(saved_value)' if boundary != 'running' else 'import threading; threading.Event().wait(120)'
        doc = store.edit_cell(notebook, 1, 'insert', text=code)
        cell = doc['cells'][0]['id']
        client = await s.client_for(notebook)
        kernel = (await client.create_kernel())['id']
        data = {'notebook_id': notebook, 'cell_id':cell, 'kernel_id':kernel,'runtime_id':s.notebooks[notebook]}
        async def checkpoint():
            temporary = path.with_suffix('.tmp'); temporary.write_text(json.dumps(data)); temporary.replace(path)
            await asyncio.Event().wait()
        if boundary == 'completed':
            commit = store._commit
            loop = asyncio.get_running_loop()
            def paused(document):
                if document['cells'] and document['cells'][0].get('execution_count') is not None:
                    asyncio.run_coroutine_threadsafe(checkpoint(), loop).result()
                return commit(document)
            store._commit = paused
        if boundary == 'running':
            raw = client.client
            async with raw._session_ready().ws_connect(raw._websocket_url(kernel, 'notebook-crash')) as observer:
                await raw._handshake(observer, 'notebook-crash')
                receipt = await service.submit(notebook, cell, 2, kernel_id=kernel, timeout=150)
                data['execution_id'] = receipt['execution_id']
                async with asyncio.timeout(20):
                    while (await raw._receive(observer))['header'].get('msg_type') != 'execute_input': pass
                await checkpoint()
        else:
            receipt = await service.submit(notebook, cell, 2, kernel_id=kernel)
            data['execution_id'] = receipt['execution_id']
            await asyncio.gather(*tuple(service.tasks.values()))
            await checkpoint()


if __name__ == '__main__':
    asyncio.run(run(Path(sys.argv[1]), sys.argv[2]))
