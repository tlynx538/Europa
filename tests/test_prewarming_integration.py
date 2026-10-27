"""Opt-in, sequential clean-pool tests on the existing private ARM64 deployment."""
import asyncio
import json
from pathlib import Path

import pytest
import pytest_asyncio

from runtime.admission import AdmissionController, AdmissionPolicy, AdmissionRejected, ResourceProfiles
from runtime.capacity import LinuxCapacityDetector
from runtime.contracts import ResourceRequests, RuntimeErrorBase
from runtime.prewarming import PrewarmingRuntimeSupervisor
from runtime.registry import RuntimeRegistry
from test_containerd_integration import sandbox, stdout as stream_output

pytestmark = pytest.mark.containerd_integration
SMALL = ResourceRequests(.25, 384 * 1024**2, 128)


def stdout(result):
    assert result['status'] == 'ok' and result['reply']['status'] == 'ok'
    return stream_output(result)


async def new_control(provider, path, target=1):
    registry = RuntimeRegistry(path, provider.recovery_scope())
    admission = AdmissionController(LinuxCapacityDetector(), ResourceProfiles({'standard': SMALL}),
                                   AdmissionPolicy(cpu_ceiling=.5, memory_ceiling=768 * 1024**2))
    control = PrewarmingRuntimeSupervisor(provider, admission, registry, target=target)
    await control.reconcile()
    return control


async def dispose(control):
    await control.pool.stop()
    for row in list(control.registry.records()):
        await control.destroy_notebook(control.registry.key(row))
    await control.close()
    control.registry.close()


async def fill(control):
    control.pool.schedule()
    await control.pool.wait()
    assert control.pool.status()['ready'] == 1, control.pool.status()
    return control.pool.rows()[0]


@pytest_asyncio.fixture
async def warmed(sandbox, tmp_path):
    provider, _ = sandbox
    await provider.assert_empty_namespace()
    control = await new_control(provider, tmp_path / 'registry/runtime.sqlite3')
    try:
        yield control, provider
    finally:
        await dispose(control)
        assert (await control.admission.metrics())['reservation_count'] == 0
        await provider.assert_empty_namespace()


@pytest.mark.parametrize('sandbox', ['runc'], indirect=True)
async def test_real_clean_warm_assignment_and_replenishment(warmed):
    control, provider = warmed
    row = await fill(control)
    identity = row['runtime_id']
    client = control._clients[identity]
    assert await client.list_kernels() == []
    info = await provider.backend.request('recovery_info', identity, **provider.ownership_parameters(identity))
    assert info['workspace_empty'] and info['limits_verified']
    assert row['notebook_id'] is None and row['kind'] == 'warm'
    assert (await control.admission.metrics())['active_notebook_runtime_count'] == 0
    first = await control.client_for('A')
    kernel = (await first.create_kernel())['id']
    assert control.notebooks['A'] == identity
    assert len(await first.list_kernels()) == 1
    assert stdout(await first.execute(kernel, "value=42; open('/workspace/A-only','w').write('A'); print(value)")) == '42\n'
    second_row = await fill(control)
    assert second_row['runtime_id'] != identity
    second = await control.client_for('B')
    other_kernel = (await second.create_kernel())['id']
    assert other_kernel != kernel and control.notebooks['B'] != identity
    result = await second.execute(other_kernel, "import os; print('value' in globals()); print(os.listdir('/workspace'))")
    assert stdout(result) == 'False\n[]\n'
    await control.pool.wait()
    assert control.pool.status()['ready'] == 0
    with pytest.raises(AdmissionRejected): await control.client_for('excess')
    assert (await control.admission.metrics())['reserved']['cpu_cores'] == .5
    assert (await control.status_for('A'))['resource_limits_enforced']
    assert (await control.status_for('B'))['resource_limits_enforced']
    target = Path('test-results/prewarming-clean-assignment.json')
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps({'clean_gateway_kernels': 0, 'workspace_empty': True,
        'initial_runtime_id': identity, 'replenished_runtime_id': second_row['runtime_id'],
        'independent_kernel_ids': [kernel, other_kernel], 'cross_notebook_state_absent': True,
        'admission': await control.admission.metrics(), 'measurements': control.pool.measurements}, indent=2)+'\n')


@pytest.mark.parametrize('sandbox', ['runc'], indirect=True)
async def test_real_concurrent_claim_and_cold_fallback(warmed):
    control, provider = warmed
    original = await fill(control)
    clients = await asyncio.gather(*(control.client_for('A') for _ in range(6)), control.client_for('B'))
    assert all(c is clients[0] for c in clients[:6])
    assert control.notebooks['A'] != control.notebooks['B']
    assert original['runtime_id'] in {control.notebooks['A'], control.notebooks['B']}
    assert len([m for m in control.pool.measurements if 'warm_claim_seconds' in m]) == 1
    for name in ['A', 'B']:
        client = await control.client_for(name)
        kernel = (await client.create_kernel())['id']
        assert stdout(await client.execute(kernel, 'print(42)')) == '42\n'
    await control.pool.wait()
    assert len((await provider.recovery_inventory())['containers']) == 2


@pytest.mark.parametrize('sandbox', ['runc'], indirect=True)
@pytest.mark.parametrize('fault', ['kernel', 'workspace', 'crashed'])
async def test_real_failed_or_contaminated_warm_is_destroyed(warmed, fault):
    control, provider = warmed
    row = await fill(control)
    identity = row['runtime_id']
    client = control._clients[identity]
    if fault == 'crashed':
        await provider.backend.request('kill', identity)
    else:
        kernel = (await client.create_kernel())['id']  # Deliberate test contamination, never normal prewarming.
        if fault == 'workspace':
            assert (await client.execute(kernel, "open('/workspace/dirty','w').write('test')"))['status'] == 'ok'
            await client.terminate_kernel(kernel)
            assert await client.list_kernels() == []
    assigned = await control.client_for('A')
    assert control.notebooks['A'] != identity
    assert control.registry.get(identity)['phase'] == 'destroyed'
    kernel = (await assigned.create_kernel())['id']
    assert stdout(await assigned.execute(kernel, "import os; print(os.listdir('/workspace'))")) == '[]\n'
    assert await provider.backend.request('inventory', identity) == {
        'containers': 0, 'tasks': 0, 'snapshot_present': False, 'network_state_present': False}


@pytest.mark.parametrize('sandbox', ['runc'], indirect=True)
async def test_real_pool_capacity_and_disabled_target(warmed):
    control, provider = warmed
    control.pool.target = 0
    await control.client_for('active')
    client = await control.client_for('active')
    kernel = (await client.create_kernel())['id']
    control.admission.policy = AdmissionPolicy(cpu_ceiling=.25, memory_ceiling=768 * 1024**2)
    control.pool.target = 1
    control.pool.schedule(); await control.pool.wait()
    assert control.pool.status()['ready'] == 0 and 'CPU' in control.pool.last_error
    assert stdout(await client.execute(kernel, 'print(42)')) == '42\n'
    assert len((await provider.recovery_inventory())['containers']) == 1


@pytest.mark.parametrize('sandbox', ['runc'], indirect=True)
async def test_real_cancel_claim_releases_only_destroyed_allocation(warmed, monkeypatch):
    control, provider = warmed
    row = await fill(control)
    client = control._clients[row['runtime_id']]
    original = client.create_kernel
    entered = asyncio.Event()
    async def pause_after_kernel(*args, **kwargs):
        result = await original(*args, **kwargs)
        entered.set()
        await asyncio.Event().wait()
        return result
    monkeypatch.setattr(client, 'create_kernel', pause_after_kernel)
    task = asyncio.create_task(control.client_for('A'))
    await asyncio.wait_for(entered.wait(), 20)
    assert control.registry.get(row['runtime_id'])['phase'] == 'claimed'
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert control.registry.get(row['runtime_id'])['phase'] == 'destroyed'
    assert await control.admission.reservation('A') is None
    assert await provider.backend.request('inventory', row['runtime_id']) == {
        'containers': 0, 'tasks': 0, 'snapshot_present': False, 'network_state_present': False}


@pytest.mark.parametrize('sandbox', ['runc'], indirect=True)
async def test_real_prewarming_http_contract(sandbox, tmp_path, monkeypatch):
    import httpx
    from main import create_app
    provider, _ = sandbox
    await provider.assert_empty_namespace()
    monkeypatch.setenv('EUROPA_WARM_TARGET', '1')
    monkeypatch.setenv('EUROPA_RUNTIME_PROVIDER', 'containerd')
    monkeypatch.delenv('EUROPA_GATEWAY_URL', raising=False)
    monkeypatch.setenv('EUROPA_RUNTIME_REGISTRY', str(tmp_path / 'registry/runtime.sqlite3'))
    monkeypatch.setenv('EUROPA_RESOURCE_PROFILES', json.dumps({'standard': vars(SMALL)}))
    monkeypatch.setenv('EUROPA_ADMISSION_CPU_CAPACITY_CORES', '.5')
    monkeypatch.setenv('EUROPA_ADMISSION_MEMORY_CAPACITY_BYTES', str(768 * 1024**2))
    app = create_app()
    async with app.router.lifespan_context(app):
        control = app.state.supervisor
        try:
            await control.pool.wait()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://europa') as http:
                assert (await http.get('/api/runtime/pool')).json()['ready'] == 1
                response = await http.post('/api/kernel/start', params={'notebook_id': 'A'})
                assert response.status_code == 201
                kernel = response.json()['id']
                result = await http.post('/api/kernel/execute', params={'notebook_id': 'A', 'kernel_id': kernel}, json={'code': 'print(42)'})
                assert result.status_code == 200 and stdout(result.json()) == '42\n'
                assert (await http.post('/api/kernel/shutdown', params={'notebook_id': 'A', 'kernel_id': kernel})).status_code == 204
        finally:
            await control.pool.stop()
            for row in list(control.registry.records()):
                await control.destroy_notebook(control.registry.key(row))
    await provider.assert_empty_namespace()


@pytest.mark.parametrize('sandbox', ['runc'], indirect=True)
@pytest.mark.parametrize('boundary', ['ready', 'created', 'started', 'claiming', 'claiming-kernel', 'active'])
async def test_real_warm_crash_recovery(sandbox, tmp_path, monkeypatch, boundary):
    import os
    import sys
    provider, _ = sandbox
    await provider.assert_empty_namespace()
    monkeypatch.setenv('EUROPA_RUNTIME_REGISTRY', str(tmp_path / 'registry/runtime.sqlite3'))
    monkeypatch.setenv('EUROPA_RESOURCE_PROFILES', json.dumps({'standard': vars(SMALL)}))
    monkeypatch.setenv('EUROPA_ADMISSION_CPU_CAPACITY_CORES', '.5')
    monkeypatch.setenv('EUROPA_ADMISSION_MEMORY_CAPACITY_BYTES', str(768 * 1024**2))
    path = tmp_path / 'checkpoint.json'
    with (tmp_path / 'child.log').open('wb') as log:
        child = await asyncio.create_subprocess_exec(sys.executable, 'tests/prewarming_process.py', str(path), boundary,
            stdout=log, stderr=log, env=dict(os.environ, PYTHONPATH=str(Path('api/src').resolve())))
        try:
            async with asyncio.timeout(40):
                while not path.exists():
                    if child.returncode is not None: raise AssertionError('Checkpoint failed; inspect private child log')
                    await asyncio.sleep(.05)
            before = json.loads(path.read_text())
        finally:
            if child.returncode is None: child.kill()
            await child.wait()
    assert child.returncode == -9
    control = await new_control(provider, tmp_path / 'registry/runtime.sqlite3')
    identity = before['records'][0]['runtime_id']
    try:
        if boundary in {'ready','started'}:
            assert control.pool.status()['ready'] == 1
            assert (await control.admission.metrics())['active_notebook_runtime_count'] == 0
            assert await control._clients[identity].list_kernels() == []
            client = await control.client_for('A')
            kernel = (await client.create_kernel())['id']
            assert stdout(await client.execute(kernel, 'print(42)')) == '42\n'
            assert control.notebooks['A'] == identity
        elif boundary == 'created':
            assert control.registry.get(identity)['phase'] == 'destroyed'
            assert (await control.admission.metrics())['reservation_count'] == 0
            await provider.assert_empty_namespace()
        elif boundary.startswith('claiming'):
            assert control.registry.get(identity)['kind'] == 'assigned'
            assert control.registry.get(identity)['phase'] == 'reconciliation'
            assert control.reconciliation_notes['A'] and not control.pool.rows()
            assert await control.admission.reservation('A') is not None
            with pytest.raises(RuntimeErrorBase): await control.client_for('A')
        else:
            client = await control.client_for('A')
            kernel = (await client.create_kernel())['id']
            assert kernel == before['records'][0]['initial_kernel_id']
            assert stdout(await client.execute(kernel, 'print(saved_value)')) == '42\n'
            assert control.notebooks['A'] == identity
    finally:
        await dispose(control)
    await provider.assert_empty_namespace()


@pytest.mark.parametrize('sandbox', ['runc'], indirect=True)
async def test_real_cold_warm_benchmark(warmed):
    import os
    import statistics
    import time
    control, provider = warmed
    count = int(os.environ.get('EUROPA_PREWARM_BENCHMARK_SAMPLES', '5'))
    if not 1 <= count <= 20: raise ValueError('Benchmark sample count must be between 1 and 20')
    samples = []
    schedule = control.pool.schedule
    for index in range(count):
        control.pool.target = 0
        await control.pool.wait()
        started = time.monotonic()
        cold = await control.client_for(f'cold-{index}')
        cold_gateway = time.monotonic() - started
        kernel_start = time.monotonic()
        kernel = (await cold.create_kernel())['id']
        cold_kernel = time.monotonic() - kernel_start
        execute_start = time.monotonic()
        assert stdout(await cold.execute(kernel, 'print(42)')) == '42\n'
        cold_execute = time.monotonic() - execute_start
        cold_total = time.monotonic() - started
        destroy_start = time.monotonic()
        await control.destroy_notebook(f'cold-{index}')
        cold_destroy = time.monotonic() - destroy_start
        await control.pool.wait()
        control.pool.target = 1
        replenish_start = time.monotonic()
        row = await fill(control)
        replenish = time.monotonic() - replenish_start
        assert await control._clients[row['runtime_id']].list_kernels() == []
        observed = await provider.backend.request('inspect', row['runtime_id'])
        assert observed['limits_verified'] and observed['memory_bytes'] > 0
        # Exclude concurrent replenishment from this paired request-latency comparison.
        # Its wall time and full capacity cost are measured separately above.
        control.pool.schedule = lambda: None
        started = time.monotonic()
        warm = await control.client_for(f'warm-{index}')
        warm_client_ready = time.monotonic() - started
        kernel = (await warm.create_kernel())['id']
        execute_start = time.monotonic()
        assert stdout(await warm.execute(kernel, 'print(42)')) == '42\n'
        warm_execute = time.monotonic() - execute_start
        warm_total = time.monotonic() - started
        destroy_start = time.monotonic()
        await control.destroy_notebook(f'warm-{index}')
        warm_destroy = time.monotonic() - destroy_start
        control.pool.schedule = schedule
        control.pool.target = 0
        samples.append({'cold_gateway_seconds': cold_gateway, 'cold_kernel_seconds': cold_kernel,
            'cold_first_execution_seconds': cold_execute, 'cold_request_to_result_seconds': cold_total,
            'warm_claim_seconds': next(m['warm_claim_seconds'] for m in reversed(control.pool.measurements) if 'warm_claim_seconds' in m),
            'warm_kernel_seconds': next(m['warm_kernel_start_seconds'] for m in reversed(control.pool.measurements) if 'warm_kernel_start_seconds' in m),
            'warm_client_ready_including_kernel_seconds': warm_client_ready,
            'warm_first_execution_seconds': warm_execute, 'warm_request_to_result_seconds': warm_total,
            'clean_warm_memory_bytes': observed['memory_bytes'], 'replenishment_seconds': replenish,
            'cold_destroy_seconds': cold_destroy, 'warm_destroy_seconds': warm_destroy})
    summary = {}
    for key in samples[0]:
        values = sorted(sample[key] for sample in samples)
        summary[key] = {'median': statistics.median(values), 'min': values[0], 'max': values[-1]}
        if count >= 20: summary[key]['empirical_p95'] = values[int(__import__('math').ceil(.95 * count)) - 1]
    target = Path('test-results/prewarming-benchmark.json')
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps({'samples_per_path': count, 'sample_order': 'cached-image cold then warm per pair',
        'replenishment_during_request_comparison': False,
        'p95_note': 'Empirical nearest-rank tail only at >=20 samples; small diagnostic sample, not a production percentile guarantee',
        'resources': vars(SMALL), 'samples': samples, 'summary': summary}, indent=2)+'\n')
