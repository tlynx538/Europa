"""Bounded runc idle lifecycle, actual kernel execution and crash probes."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time

import pytest

from kernel.gateway_client import GatewayClient, GatewayError
from runtime.lifecycle import IdlePolicy
from test_lifecycle import Clock
from test_containerd_integration import sandbox
from test_prewarming_integration import new_control, dispose, fill, stdout, SMALL

pytestmark = [pytest.mark.containerd_integration, pytest.mark.parametrize('sandbox', ['runc'], indirect=True)]


def evidence(name, data):
    directory = Path('test-results/lifecycle-validation')
    directory.mkdir(parents=True, exist_ok=True)
    (directory / (name + '.json')).write_text(json.dumps(data, indent=2) + '\n')


async def test_real_execution_disconnect_stop_and_release(sandbox, tmp_path):
    provider, _ = sandbox
    s = await new_control(provider, tmp_path / 'registry/runtime.sqlite3', target=0)
    clock = Clock(); life = s.enable_lifecycle(IdlePolicy(True, 60, 5, 10), clock=clock)
    caller = None
    try:
        client = await s.client_for('A')
        identity = s.notebooks['A']; kernel = (await client.create_kernel())['id']
        raw = client.client
        async with raw._session_ready().ws_connect(raw._websocket_url(kernel, 'idle-observer')) as observer:
            await raw._handshake(observer, 'idle-observer')
            caller = asyncio.create_task(client.execute(kernel, 'import threading; threading.Event().wait(120)', timeout=150))
            async with asyncio.timeout(15):
                while (await raw._receive(observer))['header'].get('msg_type') != 'execute_input': pass
            caller.cancel()
            with pytest.raises(asyncio.CancelledError): await caller
            clock.advance(3600); await life.sweep()
            assert s.notebooks['A'] == identity and s.registry.unfinished(identity)
            assert (await raw.inspect_kernel(kernel))['execution_state'] == 'busy'
            with pytest.raises(GatewayError) as stop: await s.destroy_notebook('A')
            assert stop.value.status_code == 409
            tasks = tuple(life.tasks)
            await client.interrupt_kernel(kernel)
            replies = await asyncio.gather(*tasks)
            assert replies[0]['reply']['ename'] == 'KeyboardInterrupt'
        await life.sweep(); clock.advance(5); await life.sweep()
        assert s.registry.get(identity)['phase'] == 'destroyed'
        assert (await s.admission.metrics())['reservation_count'] == 0
        evidence('execution-protection', {'disconnect_preserved_busy_kernel': True,
            'stop_deferred': True, 'explicit_interrupt_reply': 'KeyboardInterrupt',
            'reservation_released_after_destroy': True, 'measurements': life.measurements})
    finally:
        if caller and not caller.done(): caller.cancel(); await asyncio.gather(caller, return_exceptions=True)
        # A failed assertion should still explicitly interrupt the bounded probe before disposal.
        for identity, client in list(s._clients.items()):
            for kernel in list(client._owned):
                if kernel in client._busy: await client.interrupt_kernel(kernel)
        if life.tasks: await asyncio.gather(*tuple(life.tasks), return_exceptions=True)
        await dispose(s)


@pytest.mark.parametrize('sample', range(3))
async def test_real_periodic_idle_detection_measurement(sandbox, tmp_path, sample):
    provider, _ = sandbox
    s = await new_control(provider, tmp_path / 'registry/runtime.sqlite3', target=0)
    life = s.enable_lifecycle(IdlePolicy(True, .5, .1, .05))
    try:
        client = await s.client_for('A'); identity = s.notebooks['A']
        kernel = (await client.create_kernel())['id']
        assert stdout(await client.execute(kernel, 'print(42)')) == '42\n'
        activity = s.registry.activity(identity, time.time())
        observed = await provider.backend.request('inspect', identity)
        before = time.monotonic()
        life.start()
        async with asyncio.timeout(20):
            while 'A' in s.notebooks:
                await asyncio.sleep(.02)  # Bounded observation, not a race workaround.
        elapsed = time.monotonic() - before
        assert (await s.admission.metrics())['reservation_count'] == 0
        assert not s.registry.unfinished(identity)
        evidence(f'periodic-idle-{sample}', {'policy': vars(life.policy), 'last_activity': activity['last_activity'],
            'observation_to_release_seconds': elapsed, 'measurements': life.measurements,
            'idle_memory_bytes': observed['memory_bytes'], 'limits_verified': observed['limits_verified'],
            'reserved_memory_bytes_until_destroy': SMALL.memory_bytes})
    finally:
        await dispose(s)


async def test_real_leases_and_warm_replenishment(sandbox, tmp_path):
    provider, _ = sandbox
    s = await new_control(provider, tmp_path / 'registry/runtime.sqlite3')
    clock = Clock(); life = s.enable_lifecycle(IdlePolicy(True, 60, 0, 10), clock=clock)
    try:
        await fill(s)
        client = await s.client_for('A')
        identity = s.notebooks['A']
        kernel = (await client.create_kernel())['id']
        assert stdout(await client.execute(kernel, 'print(42)')) == '42\n'
        await fill(s)
        ready = s.pool.rows()[0]['runtime_id']
        lease = await life.lease('A', 'agent', 120)
        clock.advance(65); await life.sweep()
        assert s.notebooks['A'] == identity
        await life.release('A', lease['lease_token']); await life.sweep()
        await s.pool.wait()
        assert s.registry.get(identity)['phase'] == 'destroyed'
        assert s.pool.status()['ready'] == 1 and s.pool.rows()[0]['runtime_id'] == ready
        # Consume the surviving slot, then idle retirement frees capacity for a new clean slot.
        other = await s.client_for('B')
        assert stdout(await other.execute((await other.create_kernel())['id'], 'print(43)')) == '43\n'
        await s.pool.wait(); clock.advance(61); await life.sweep(); await s.pool.wait()
        assert s.pool.status()['ready'] == 1
        assert all(r['kind'] == 'warm' for r in s.registry.records())
        assert (await s.admission.metrics())['reservation_count'] == 1
    finally:
        await dispose(s)


@pytest.mark.parametrize('boundary', ['execution', 'deleting', 'deleted'])
async def test_real_idle_crash_reconciliation(sandbox, tmp_path, monkeypatch, boundary):
    provider, _ = sandbox
    await provider.assert_empty_namespace()
    path = tmp_path / 'registry/runtime.sqlite3'
    monkeypatch.setenv('EUROPA_RUNTIME_REGISTRY', str(path))
    monkeypatch.setenv('EUROPA_WARM_TARGET', '0')
    monkeypatch.setenv('EUROPA_RESOURCE_PROFILES', json.dumps({'standard': vars(SMALL)}))
    monkeypatch.setenv('EUROPA_ADMISSION_CPU_CAPACITY_CORES', '.5')
    monkeypatch.setenv('EUROPA_ADMISSION_MEMORY_CAPACITY_BYTES', str(768 * 1024**2))
    checkpoint = tmp_path / 'checkpoint.json'
    with (tmp_path / 'child.log').open('wb') as log:
        child = await asyncio.create_subprocess_exec(sys.executable, 'tests/lifecycle_process.py', str(checkpoint), boundary,
            stdout=log, stderr=log, env=dict(os.environ, PYTHONPATH=str(Path('api/src').resolve())))
        try:
            async with asyncio.timeout(45):
                while not checkpoint.exists():
                    if child.returncode is not None: raise AssertionError('Private checkpoint log records startup failure')
                    await asyncio.sleep(.05)
            before = json.loads(checkpoint.read_text())
        finally:
            if child.returncode is None: child.kill()
            await child.wait()
    assert child.returncode == -9
    s = await new_control(provider, path, target=0)
    identity = before['records'][0]['runtime_id']
    life = s.enable_lifecycle(IdlePolicy(True, .01, 0, 1))
    try:
        await life.sweep()
        if boundary == 'execution':
            assert s.registry.get(identity)['phase'] == 'reconciliation'
            assert s.registry.unfinished(identity) and s.recovery_blockers
            assert (await s.admission.metrics())['reservation_count'] == 1
            assert (await provider.backend.request('inspect', identity))['state'] == 'running'
            handle = await provider.recover(s.registry.get(identity))
            probe = GatewayClient(handle.endpoint.url, handle.endpoint.token)
            await probe.__aenter__()
            try:
                models = await probe.list_kernels()
                assert len(models) == 1 and models[0]['execution_state'] == 'busy'
            finally:
                await probe.disconnect()
            await s.destroy_notebook('default')  # Explicit operator teardown of uncertain work.
        else:
            assert s.registry.get(identity)['phase'] == 'destroyed'
        assert (await s.admission.metrics())['reservation_count'] == 0
        await provider.assert_empty_namespace()
    finally:
        await dispose(s)


async def test_real_graceful_drain_preserves_busy_kernel(sandbox, tmp_path):
    provider, _ = sandbox
    s = await new_control(provider, tmp_path / 'registry/runtime.sqlite3', target=0)
    life = s.enable_lifecycle(IdlePolicy(True, 60, 0, 30))
    caller = None
    try:
        client = await s.client_for('A')
        identity = s.notebooks['A']; kernel = (await client.create_kernel())['id']
        endpoint = s.handles[identity].endpoint
        raw = client.client
        async with raw._session_ready().ws_connect(raw._websocket_url(kernel, 'drain-observer')) as observer:
            await raw._handshake(observer, 'drain-observer')
            caller = asyncio.create_task(client.execute(kernel, 'import threading; threading.Event().wait(120)', timeout=150))
            async with asyncio.timeout(15):
                while (await raw._receive(observer))['header'].get('msg_type') != 'execute_input': pass
            await s.close()
            with pytest.raises(asyncio.CancelledError): await caller
        assert s.registry.unfinished(identity)
        probe = GatewayClient(endpoint.url, endpoint.token)
        await probe.__aenter__()
        try:
            models = await probe.list_kernels()
            assert len(models) == 1 and models[0]['id'] == kernel and models[0]['execution_state'] == 'busy'
        finally:
            await probe.disconnect()
        assert (await s.admission.metrics())['reservation_count'] == 1
    finally:
        if caller and not caller.done(): caller.cancel(); await asyncio.gather(caller, return_exceptions=True)
        await dispose(s)
