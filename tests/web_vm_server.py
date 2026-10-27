"""Opt-in bounded UI probe server; use only on the approved isolated Lima VM.

Uses the production app and APIs. On shutdown explicitly disposes this probe's
allocations, unlike normal production shutdown's durable-runtime preservation.
No runtime, daemon or networking configuration is installed or modified.
"""
import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import sys

import uvicorn

from main import create_app


async def main():
    if os.environ.get('EUROPA_WEB_VM_PROBE') != '1':
        raise RuntimeError('Explicit VM probe opt-in required')
    root = Path(sys.argv[1])
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    (root / 'server.pid').write_text(str(os.getpid()))
    os.environ.update(EUROPA_NOTEBOOK_ROOT=str(root / 'notebooks'),
                      EUROPA_APPLICATION_ROOT=str(root / 'application'),
                      EUROPA_RUNTIME_REGISTRY=str(root / 'registry' / 'runtime.sqlite3'),
                      EUROPA_WARM_TARGET='0')
    app = create_app()
    original = app.router.lifespan_context

    @asynccontextmanager
    async def probe_lifespan(application):
        async with original(application):
            try:
                yield
            finally:
                s = application.state.supervisor
                await application.state.notebook_service.close(grace=5)
                await s.pool.stop()
                for row in s.registry.records():
                    if row['phase'] != 'destroyed':
                        await s.destroy_notebook(s.registry.key(row))

    app.router.lifespan_context = probe_lifespan
    # Bounded lifespan even if the external browser runner fails.
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=18200, log_level='warning'))
    async def expire():
        await asyncio.sleep(180)
        server.should_exit = True
    timer = asyncio.create_task(expire())
    try:
        await server.serve()
    finally:
        timer.cancel()
        await asyncio.gather(timer, return_exceptions=True)


if __name__ == '__main__':
    asyncio.run(main())
