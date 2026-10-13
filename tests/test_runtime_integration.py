import asyncio
import json
import time
from pathlib import Path

import httpx
import psutil
import pytest
import pytest_asyncio

from main import create_app
from runtime.contracts import ResourceRequests, RuntimeErrorBase, RuntimeSpec, RuntimeState
from runtime.local import LocalRuntimeProvider, alive
from runtime.supervisor import RuntimeSupervisor

pytestmark = pytest.mark.integration
METRICS = []


def stdout(result):
    return "".join(m["content"]["text"] for m in result["messages"]
                   if m["header"]["msg_type"] == "stream" and m["content"]["name"] == "stdout")


@pytest_asyncio.fixture
async def provider(tmp_path, request):
    if not request.config.getoption("--run-local-gateway"):
        pytest.skip("requires --run-local-gateway")
    import sys
    assert Path(sys.prefix).resolve() == (Path(__file__).resolve().parents[1] / ".venv").resolve()
    provider = LocalRuntimeProvider(root=tmp_path)
    processes = []
    try:
        yield provider
    finally:
        for runtime_id in provider._runtimes:
            processes.extend(provider.processes(runtime_id))
        await provider.close()
        assert not any(alive(process) for process in processes)
        assert not any(m.get("unreclaimed_processes") for m in provider.measurements)
        METRICS.extend(provider.measurements)
        target = Path("test-results")
        target.mkdir(exist_ok=True)
        (target / "runtime-timings.json").write_text(json.dumps(METRICS, indent=2) + "\n")


async def test_provider_lifecycle_and_generation(provider):
    handle = await provider.provision(RuntimeSpec("lifecycle", resources=ResourceRequests(1, 128 * 1024 * 1024)))
    processes = provider.processes(handle.runtime_id)
    status = await provider.inspect(handle.runtime_id)
    assert status.ready and status.process_alive
    assert not status.resource_limits_enforced
    assert await provider.start(handle.runtime_id) == handle
    await provider.stop(handle.runtime_id)
    await provider.stop(handle.runtime_id)
    assert not any(alive(process) for process in processes)
    assert (await provider.inspect(handle.runtime_id)).handle.state == RuntimeState.STOPPED
    restarted = await provider.start(handle.runtime_id)
    assert restarted.generation == handle.generation + 1
    assert restarted.endpoint != handle.endpoint
    await provider.destroy(handle.runtime_id)
    await provider.destroy(handle.runtime_id)
    await provider.stop(handle.runtime_id)
    assert (await provider.inspect(handle.runtime_id)).handle.state == RuntimeState.DESTROYED
    with pytest.raises(RuntimeErrorBase):
        await provider.start(handle.runtime_id)


async def test_two_notebooks_and_duplicate_provisioning(provider):
    supervisor = RuntimeSupervisor({"local": provider})
    try:
        handles = await asyncio.gather(*(supervisor.ensure(RuntimeSpec("A")) for _ in range(8)))
        a = handles[0]
        assert all(handle == a for handle in handles)
        b = await supervisor.ensure(RuntimeSpec("B"))
        assert a.runtime_id != b.runtime_id and a.endpoint.url != b.endpoint.url
        assert len(provider._runtimes) == 2
        ca, cb = await supervisor.client_for("A"), await supervisor.client_for("B")
        start = time.monotonic()
        ka, kb = await asyncio.gather(ca.create_kernel(), cb.create_kernel())
        METRICS.append({"kernel_pair_startup_seconds": time.monotonic() - start})
        await ca.execute(ka["id"], "value = 'A'")
        assert stdout(await cb.execute(kb["id"], "print('value' in globals())")) == "False\n"
        await cb.execute(kb["id"], "value = 'B'")
        assert stdout(await ca.execute(ka["id"], "print(value)")) == "A\n"
        for _ in range(3):
            start = time.monotonic()
            assert stdout(await cb.execute(kb["id"], "print(value)")) == "B\n"
            METRICS.append({"warm_execution_seconds": time.monotonic() - start})
        pa = provider.processes(a.runtime_id)
        await supervisor.destroy_notebook("A")
        assert not any(alive(process) for process in pa)
        assert (await supervisor.inspect(b.runtime_id)).ready
        assert stdout(await cb.execute(kb["id"], "print(value)")) == "B\n"
        await supervisor.destroy_notebook("A")
    finally:
        await supervisor.close()


async def test_failed_startup_removes_owned_environment(provider, tmp_path):
    config = tmp_path / "failure.py"
    config.write_text("raise RuntimeError('trusted startup failure')\n")
    provider.config_path = config
    with pytest.raises(RuntimeErrorBase, match="startup failed"):
        await provider.provision(RuntimeSpec("failure"))
    assert all(r.handle.state == RuntimeState.DESTROYED for r in provider._runtimes.values())
    assert not list(tmp_path.glob("europa-runtime-*"))


async def test_startup_timeout_reaps_process(provider, monkeypatch):
    async def never_ready(record):
        await asyncio.sleep(2)
    monkeypatch.setattr(provider, "_ready", never_ready)
    with pytest.raises(RuntimeErrorBase, match="timed out"):
        await provider.provision(RuntimeSpec("timeout", startup_timeout=0.1))
    assert all(r.process is None and r.handle.state == RuntimeState.DESTROYED for r in provider._runtimes.values())


async def test_cancelled_startup_cleans_up(provider, monkeypatch):
    spawned = asyncio.Event()
    async def wait_until_cancelled(record):
        spawned.set()
        await asyncio.sleep(2)
    monkeypatch.setattr(provider, "_ready", wait_until_cancelled)
    task = asyncio.create_task(provider.provision(RuntimeSpec("cancel")))
    await asyncio.wait_for(spawned.wait(), 3)
    processes = provider.processes(next(iter(provider._runtimes)))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not any(alive(process) for process in processes)
    assert all(r.handle.state == RuntimeState.DESTROYED for r in provider._runtimes.values())


async def test_gateway_death_reclaims_kernel_and_rejects_stale_handle(provider):
    supervisor = RuntimeSupervisor({"local": provider})
    try:
        handle = await supervisor.ensure(RuntimeSpec("death"))
        client = await supervisor.client_for("death")
        kernel = (await client.create_kernel())["id"]
        pid = int(stdout(await client.execute(kernel, "import os; print(os.getpid())")))
        kernel_process = psutil.Process(pid)
        # Exercise crash recovery without calling Gateway's graceful shutdown.
        process = provider._runtimes[handle.runtime_id].process
        process.kill()
        await process.wait()
        status = await supervisor.inspect(handle.runtime_id)
        assert not status.ready and not status.process_alive
        assert status.handle.state == RuntimeState.FAILED
        assert not alive(kernel_process)
        with pytest.raises(RuntimeErrorBase, match="not ready"):
            await supervisor.client_for("death")
    finally:
        await supervisor.close()


async def test_application_owns_two_runtime_teardown(provider):
    app = create_app(runtime_provider=provider)
    processes = []
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://europa") as http:
            for notebook_id in ["default", "other"]:
                params = {"notebook_id": notebook_id}
                kernel = (await http.post("/api/kernel/start", params=params)).json()["id"]
                result = await http.post("/api/kernel/execute", params={**params, "kernel_id": kernel},
                                         json={"code": "print(42)"})
                assert result.status_code == 200 and stdout(result.json()) == "42\n"
                status = (await http.get("/api/runtime", params=params)).json()
                assert status["ready"] and "endpoint" not in status and "token" not in status
                processes.extend(provider.processes(status["runtime_id"]))
            assert (await http.delete("/api/runtime", params={"notebook_id": "default"})).status_code == 204
            assert (await http.get("/api/runtime", params={"notebook_id": "other"})).json()["ready"]
    assert not any(alive(process) for process in processes)
    assert not app.state.supervisor.handles


async def test_shutdown_waits_for_inflight_provision(provider, monkeypatch):
    ready = asyncio.Event()
    release = asyncio.Event()
    original = provider._ready

    async def delayed_ready(record):
        await original(record)
        ready.set()
        await release.wait()
    monkeypatch.setattr(provider, "_ready", delayed_ready)
    supervisor = RuntimeSupervisor({"local": provider})
    provisioning = asyncio.create_task(supervisor.ensure(RuntimeSpec("inflight")))
    closing = None
    try:
        await asyncio.wait_for(ready.wait(), 5)
        closing = asyncio.create_task(supervisor.close())
        await asyncio.sleep(0.01)
        assert not closing.done()
        release.set()
        await provisioning
        await closing
        assert not supervisor.handles
        assert all(r.handle.state == RuntimeState.DESTROYED for r in provider._runtimes.values())
    finally:
        release.set()
        await asyncio.gather(provisioning, return_exceptions=True)
        if closing:
            await closing
        await supervisor.close()


async def test_failed_cleanup_retains_ownership_for_retry(provider, monkeypatch):
    supervisor = RuntimeSupervisor({"local": provider})
    handle = await supervisor.ensure(RuntimeSpec("cleanup-retry"))
    processes = provider.processes(handle.runtime_id)
    original = provider._stop

    async def failed_stop(record):
        raise RuntimeErrorBase("trusted cleanup failure")
    monkeypatch.setattr(provider, "_stop", failed_stop)
    try:
        with pytest.raises(RuntimeErrorBase, match="cleanup failure"):
            await supervisor.destroy_notebook("cleanup-retry")
        assert provider._runtimes[handle.runtime_id].process is not None
        assert handle.runtime_id in supervisor.handles
        assert not (await supervisor.inspect(handle.runtime_id)).ready
        with pytest.raises(RuntimeErrorBase, match="not ready"):
            await supervisor.client_for("cleanup-retry")
    finally:
        monkeypatch.setattr(provider, "_stop", original)
        await supervisor.close()
    assert not any(alive(process) for process in processes)
