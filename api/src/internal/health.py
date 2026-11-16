"""Side-effect-free top-level health: the API's own storage plus the observed Gateway state.

The Gateway state is read from the supervisor's live ownership tables (handles,
bound clients, unavailable set, provisioning locks). Only a runtime that already
has a bound client is probed, with one read-only authenticated request
(GatewayClient.health, GET /api/kernelspecs) bounded by PROBE_SECONDS. This never
calls client_for, ensure, attach, inspect or any provider method, so it cannot
start, attach or clean up a Gateway, runtime or kernel. `GET /api/health` is separate
and unchanged (it provisions the default runtime).

The body names states and components only: no paths, URLs, ports, tokens,
runtime/kernel identities or exception text.
"""
import asyncio
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
PROBE_SECONDS = 2.0
STORAGE_SECONDS = 2.0
# Aggregate across runtimes: the most severe observed state wins.
SEVERITY = ('unreachable', 'failed', 'stopping', 'starting', 'running')


def _application_ok(store):
    with store.mutex:  # read-only; avoids transaction(), which rescans and writes the catalog
        if store.db is None:
            return False
        store.db.execute('SELECT count(*) FROM sqlite_master').fetchone()
        return True


def _notebooks_ok(store):
    with store.mutex:
        if store.fd is None:
            return False
        return os.fstat(store.fd).st_nlink > 0  # 0: the storage directory was removed


async def _check(function, store):
    if store is None:
        return False
    try:
        return await asyncio.wait_for(asyncio.to_thread(function, store), STORAGE_SECONDS)
    except Exception:  # sqlite3/OS errors or timeout; details may contain paths
        return False


async def _probe(client):
    try:
        async with asyncio.timeout(PROBE_SECONDS):
            await client.health()
        return 'running'
    except Exception:
        return 'unreachable'


async def gateway_state(supervisor):
    """'stopped' when no Gateway runtime is owned or attached; otherwise the most severe
    of running / starting / stopping / failed / unreachable across owned runtimes."""
    if supervisor is None:
        return 'stopped'
    states, probes = [], []
    for runtime_id, handle in tuple(supervisor.handles.items()):
        client = supervisor._clients.get(runtime_id)
        if runtime_id in supervisor._unavailable:
            states.append('stopping')
        elif handle.state == 'starting' or client is None:
            states.append('starting')
        elif handle.state != 'ready':
            states.append('failed')
        elif getattr(client, '_closing', False):
            states.append('stopping')
        else:
            probes.append(_probe(client))
    if not states and not probes:
        provisioning = any(lock.locked() for lock in tuple(supervisor._locks.values()))
        return 'starting' if provisioning else 'stopped'
    states += await asyncio.gather(*probes)
    return min(states, key=SEVERITY.index)


def _agents(app):
    runtime = getattr(app.state, 'agent_runtime', None)
    return 'running' if runtime is not None and not runtime.closed else 'disabled'


@router.api_route('/health', methods=['GET', 'HEAD'], include_in_schema=False)
async def health(request: Request):
    app = request.app
    checks = {'application_storage': await _check(_application_ok, getattr(app.state, 'application_store', None)),
              'notebook_storage': await _check(_notebooks_ok, getattr(app.state, 'notebook_store', None))}
    failing = [name for name, ok in checks.items() if not ok]
    body = {'status': 'unavailable' if failing else 'ok', 'api': 'unavailable' if failing else 'ok',
            'gateway': await gateway_state(getattr(app.state, 'supervisor', None)), 'agents': _agents(app)}
    if failing:
        body['unavailable'] = failing
    return JSONResponse(body, status_code=503 if failing else 200, headers={'Cache-Control': 'no-store'})
