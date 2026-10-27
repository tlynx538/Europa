"""Bounded, opt-in admission tests against the existing private runc deployment."""
import asyncio
import json
from pathlib import Path

import pytest
import pytest_asyncio

from runtime.admission import AdmissionController, AdmissionPolicy, AdmissionRejected, ResourceProfiles
from runtime.capacity import LinuxCapacityDetector
from runtime.contracts import ResourceRequests, RuntimeErrorBase, RuntimeSpec
from runtime.supervisor import RuntimeSupervisor
from test_containerd_integration import sandbox, stdout

pytestmark = pytest.mark.containerd_integration
SMALL = ResourceRequests(.5, 384 * 1024**2, 128)


@pytest_asyncio.fixture
async def admitted(sandbox):
    provider, _ = sandbox
    await provider.assert_empty_namespace()
    admission = AdmissionController(LinuxCapacityDetector(),
        ResourceProfiles({'standard': SMALL, 'compact': ResourceRequests(.25, 384 * 1024**2, 96)}),
        AdmissionPolicy(memory_ceiling=768 * 1024**2, cpu_ceiling=.5))
    supervisor = RuntimeSupervisor({'containerd': provider}, default_provider='containerd', admission=admission)
    try:
        yield supervisor, provider, admission
    finally:
        await supervisor.close()
        assert (await admission.metrics())['reservation_count'] == 0
        await provider.assert_empty_namespace()


@pytest.mark.parametrize('sandbox', ['runc'], indirect=True)
async def test_real_admission_concurrency_and_profiles(admitted):
    supervisor, provider, admission = admitted
    results = await asyncio.gather(*(supervisor.client_for(name) for name in ['A', 'B']), return_exceptions=True)
    assert sum(isinstance(r, AdmissionRejected) for r in results) == 1
    metrics = await admission.metrics()
    assert metrics['reserved']['cpu_cores'] == .5 and metrics['reservation_count'] == 1
    winner = next(iter(supervisor.notebooks))
    clients = await asyncio.gather(*(supervisor.client_for(winner) for _ in range(8)))
    assert all(c is clients[0] for c in clients)
    assert len(provider._runtimes) == 1
    await supervisor.destroy_notebook(winner)
    # Two distinct smaller profiles fit exactly and remain independently usable.
    first = await supervisor.client_for('C', resource_profile='compact')
    second = await supervisor.client_for('D', resource_profile='compact')
    assert first is not second
    assert (await admission.metrics())['reserved']['cpu_cores'] == .5
    await supervisor.destroy_notebook('C')
    kernel = (await second.create_kernel())['id']
    assert stdout(await second.execute(kernel, 'print(42)')) == '42\n'
    assert (await supervisor.status_for('D'))['resource_limits_enforced']
    await second.terminate_kernel(kernel)


@pytest.mark.parametrize('sandbox', ['runc'], indirect=True)
@pytest.mark.parametrize('mode', ['startup_failure', 'cancel', 'teardown_failure', 'crash'])
async def test_real_admission_failure_ownership(admitted, mode):
    supervisor, provider, admission = admitted
    if mode in {'startup_failure', 'cancel'}:
        original = provider._ready
        entered = asyncio.Event()
        async def fail_or_wait(record):
            entered.set()
            if mode == 'startup_failure':
                raise RuntimeErrorBase('Injected failure after actual task start')
            await asyncio.Event().wait()
        provider._ready = fail_or_wait
        try:
            provisioning = asyncio.create_task(supervisor.client_for('A'))
            await asyncio.wait_for(entered.wait(), 20)
            assert (await admission.metrics())['reservation_count'] == 1
            if mode == 'cancel':
                provisioning.cancel()
            with pytest.raises(asyncio.CancelledError if mode == 'cancel' else RuntimeErrorBase):
                await provisioning
        finally:
            provider._ready = original
        assert (await admission.metrics())['reservation_count'] == 0
        await provider.assert_empty_namespace()
        return
    await supervisor.client_for('A')
    identity = supervisor.notebooks['A']
    if mode == 'crash':
        await provider.backend.request('kill', identity)
        async with asyncio.timeout(5):
            while (await supervisor.status_for('A'))['ready']:
                await asyncio.sleep(.05)
        assert (await admission.reservation('A'))['phase'] == 'cleanup_pending'
    else:
        original = provider.backend.request
        async def fail_destroy(operation, runtime_id, **kwargs):
            if operation == 'destroy':
                raise RuntimeErrorBase('Injected backend destruction failure')
            return await original(operation, runtime_id, **kwargs)
        provider.backend.request = fail_destroy
        try:
            with pytest.raises(RuntimeErrorBase):
                await supervisor.destroy_notebook('A')
            assert (await original('inspect', identity))['state'] == 'running'
            assert (await admission.metrics())['reservation_cleanup_failures'] == 1
        finally:
            provider.backend.request = original
    with pytest.raises(AdmissionRejected):
        await supervisor.client_for('B')
    assert (await admission.metrics())['reserved']['cpu_cores'] == .5
    await supervisor.destroy_notebook('A')
    assert (await admission.metrics())['reservation_count'] == 0
    await provider.assert_empty_namespace()


@pytest.mark.parametrize('sandbox', ['runc'], indirect=True)
async def test_real_admitted_limit_controls(admitted):
    supervisor, provider, admission = admitted
    client = await supervisor.client_for('A')
    identity = supervisor.notebooks['A']
    kernel = (await client.create_kernel())['id']
    # /proc/self/cgroup identifies the actual task, not just the requested OCI spec.
    result = await client.execute(kernel, "print(open('/proc/self/cgroup').read(), end='')")
    relative = stdout(result).strip().removeprefix('0::').lstrip('/')
    cgroup = Path('/sys/fs/cgroup') / relative
    assert identity in relative and '..' not in Path(relative).parts
    controls = {name: (cgroup / name).read_text().strip() for name in ['cpu.max', 'memory.max', 'pids.max']}
    assert controls == {'cpu.max': '50000 100000', 'memory.max': '402653184', 'pids.max': '128'}
    def counter(file, name):
        return dict(line.split() for line in (cgroup / file).read_text().splitlines()).get(name, '0')
    before = int(counter('cpu.stat', 'nr_throttled'))
    result = await client.execute(kernel, r'''import subprocess, sys, json
code="import time\nend=time.monotonic()+1\nwhile time.monotonic()<end: pass"
children=[subprocess.Popen([sys.executable,'-c',code]) for _ in range(2)]
try: codes=[p.wait(timeout=10) for p in children]
finally:
    for p in children:
        if p.poll() is None: p.kill()
    for p in children: p.wait()
print(json.dumps(codes))''')
    assert json.loads(stdout(result)) == [0, 0]
    throttle = int(counter('cpu.stat', 'nr_throttled')) - before
    assert throttle > 0
    before = int(counter('pids.events', 'max'))
    result = await client.execute(kernel, '''import subprocess, json
children=[]; denied=None
try:
    for _ in range(160):
        try: children.append(subprocess.Popen(['/bin/sleep','10']))
        except OSError as e: denied=e.errno; break
finally:
    for p in children: p.terminate()
    for p in children: p.wait()
print(json.dumps({'spawned':len(children),'errno':denied}))''')
    pid_probe = json.loads(stdout(result))
    assert pid_probe['errno'] == 11 and int(counter('pids.events', 'max')) > before
    before = int(counter('memory.events', 'oom_kill'))
    result = await client.execute(kernel, '''import subprocess, sys
p=subprocess.run([sys.executable,'-c','data=bytearray(512*1024**2)'], timeout=10)
print(p.returncode)''')
    assert stdout(result) == '-9\n'
    oom = int(counter('memory.events', 'oom_kill')) - before
    assert oom > 0
    assert stdout(await client.execute(kernel, 'print(42)')) == '42\n'
    await client.terminate_kernel(kernel)
    evidence = {'controls': controls, 'admission': await admission.metrics(),
                'cpu_throttled_periods_delta': throttle, 'pid_probe': pid_probe,
                'oom_kill_delta': oom, 'gateway_survived': True}
    target = Path('test-results/admission-enforcement.json')
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(evidence, indent=2) + '\n')


@pytest.mark.parametrize('sandbox', ['runc'], indirect=True)
async def test_real_admission_http(sandbox, monkeypatch, tmp_path):
    import httpx
    from main import create_app
    provider, _ = sandbox
    await provider.assert_empty_namespace()
    monkeypatch.setenv('EUROPA_RUNTIME_PROVIDER', 'containerd')
    monkeypatch.setenv('EUROPA_WARM_TARGET', '0')
    # Durable control-plane shutdown preserves containers; this test owns a private registry.
    monkeypatch.setenv('EUROPA_RUNTIME_REGISTRY', str(tmp_path / 'registry/runtime.sqlite3'))
    monkeypatch.delenv('EUROPA_GATEWAY_URL', raising=False)
    monkeypatch.setenv('EUROPA_RESOURCE_PROFILES', json.dumps({
        'standard': {'cpu_cores': .5, 'memory_bytes': 384 * 1024**2, 'pids_limit': 128},
        'compact': {'cpu_cores': .25, 'memory_bytes': 384 * 1024**2, 'pids_limit': 96}}))
    monkeypatch.setenv('EUROPA_ADMISSION_CPU_CAPACITY_CORES', '.5')
    app = create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://europa') as http:
            status = (await http.get('/api/runtime')).json()
            assert status['admission']['profile_id'] == 'standard' and status['resource_limits_enforced']
            metrics = (await http.get('/api/runtime/admission')).json()
            assert metrics['reserved']['cpu_cores'] == .5
            invalid = await http.post('/api/runtime/provision', params={'notebook_id': 'invalid', 'resource_profile': ' '})
            assert invalid.status_code == 422
            for profile in ['standard', 'unknown']:
                rejected = await http.post('/api/runtime/provision', params={'notebook_id': 'blocked', 'resource_profile': profile})
                assert rejected.status_code == 503
            assert (await http.get('/api/runtime/admission')).json()['admission_rejections'] == 2
            assert (await http.delete('/api/runtime')).status_code == 204
            for name in ['A', 'B']:
                response = await http.post('/api/runtime/provision', params={'notebook_id': name, 'resource_profile': 'compact'})
                assert response.status_code == 201
            kernel = (await http.post('/api/kernel/start', params={'notebook_id': 'B'})).json()['id']
            assert (await http.delete('/api/runtime', params={'notebook_id': 'A'})).status_code == 204
            result = await http.post('/api/kernel/execute', params={'notebook_id': 'B', 'kernel_id': kernel}, json={'code': 'print(42)'})
            assert result.status_code == 200 and stdout(result.json()) == '42\n'
            assert (await http.get('/api/runtime/admission')).json()['active_notebook_runtime_count'] == 1
            assert (await http.delete('/api/runtime', params={'notebook_id': 'B'})).status_code == 204
    await provider.assert_empty_namespace()
    # Independently exercise the actual built-in defaults without legacy overrides.
    for name in ['EUROPA_RESOURCE_PROFILES', 'EUROPA_RESOURCE_DEFAULT_PROFILE',
                 'EUROPA_CONTAINERD_CPU_CORES', 'EUROPA_CONTAINERD_MEMORY_BYTES', 'EUROPA_CONTAINERD_PIDS_LIMIT']:
        monkeypatch.delenv(name, raising=False)
    app = create_app()
    async with app.router.lifespan_context(app):
        supervisor = app.state.supervisor
        status = await supervisor.status_for('default')
        assert status['resource_limits_enforced']
        assert status['admission']['resources'] == {'cpu_cores': .5, 'memory_bytes': 512 * 1024**2, 'pids_limit': 128}
        client = await supervisor.client_for()
        kernel = (await client.create_kernel())['id']
        assert stdout(await client.execute(kernel, 'print(42)')) == '42\n'
        await client.terminate_kernel(kernel)
        await supervisor.destroy_notebook('default')
    await provider.assert_empty_namespace()
