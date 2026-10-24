"""Disposable fenced control plane for clean-pool SIGKILL checkpoints."""
import asyncio
import json
import os
from pathlib import Path
import sys

from runtime.admission_config import configured_admission
from runtime.containerd import ContainerdConfig, ContainerdRuntimeProvider
from runtime.containerd_backend import SDKBridgeBackend
from runtime.prewarming import PrewarmingRuntimeSupervisor
from runtime.registry import RuntimeRegistry


async def run(path, mode):
    backend = SDKBridgeBackend(Path(os.environ['EUROPA_CONTAINERD_BRIDGE']),
        socket=os.environ.get('EUROPA_CONTAINERD_SOCKET', '/run/containerd/containerd.sock'),
        cni_config=os.environ['EUROPA_CONTAINERD_CNI_CONFIG'], cni_bin=os.environ['EUROPA_CONTAINERD_CNI_BIN'])
    provider = ContainerdRuntimeProvider(backend, ContainerdConfig(os.environ['EUROPA_CONTAINERD_IMAGE']))
    admission, guard = configured_admission(backend.socket)
    guard.acquire()
    registry = RuntimeRegistry(os.environ['EUROPA_RUNTIME_REGISTRY'], provider.recovery_scope())
    control = PrewarmingRuntimeSupervisor(provider, admission, registry)
    await control.reconcile()
    async def checkpoint():
        data = {'records': [{k: row[k] for k in ('runtime_id','notebook_id','kind','phase','generation','initial_kernel_id')}
                            for row in registry.records()]}
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data)); temporary.replace(path)
        await asyncio.Event().wait()
    if mode == 'created':
        request = backend.request
        async def pause_create(operation, identity, **kwargs):
            result = await request(operation, identity, **kwargs)
            if operation == 'create': await checkpoint()
            return result
        backend.request = pause_create
    if mode == 'started':
        ready = provider._ready
        async def pause_ready(record):
            await ready(record)
            await checkpoint()
        provider._ready = pause_ready
    control.pool.schedule(); await control.pool.wait()
    assert control.pool.status()['ready'] == 1
    if mode in {'claiming','claiming-kernel','active'}:
        # Keep this checkpoint to one test runtime; replenishment is tested separately.
        control.pool.schedule = lambda: None
        row = control.pool.rows()[0]
        client = control._clients[row['runtime_id']]
        original = client.create_kernel
        if mode != 'active':
            async def pause_claim(*args, **kwargs):
                if mode == 'claiming': await checkpoint()
                result = await original(*args, **kwargs)
                await checkpoint()
                return result
            client.create_kernel = pause_claim
        await control.client_for('A')
        if mode == 'active':
            kernel = registry.get(row['runtime_id'])['initial_kernel_id']
            assert (await client.execute(kernel, 'saved_value=42'))['status'] == 'ok'
    await checkpoint()


if __name__ == '__main__':
    asyncio.run(run(Path(sys.argv[1]), sys.argv[2]))
