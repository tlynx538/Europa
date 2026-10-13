"""Controlled backend tests: no container isolation or enforcement claims."""
import asyncio

import pytest

from runtime.containerd import ContainerdConfig, ContainerdRuntimeProvider, HANDLERS
from runtime.contracts import ResourceRequests, RuntimeErrorBase, RuntimeSpec, RuntimeState
from runtime.supervisor import RuntimeSupervisor

IMAGE = "registry.example/europa/gateway@sha256:" + "a" * 64


class FakeBackend:
    def __init__(self, fail=None):
        self.calls = []
        self.containers = {}
        self.tasks = {}
        self.networks = set()
        self.snapshots = set()
        self.fail = fail
        self.force = False

    async def request(self, operation, runtime_id, **parameters):
        self.calls.append((operation, runtime_id, parameters))
        if operation == "image":
            if self.fail == operation:
                raise RuntimeErrorBase("injected image failure")
            return {"cached": True}
        if operation == "create":
            self.snapshots.add(runtime_id)
            if self.fail == operation:
                raise RuntimeErrorBase("injected partial create failure")
            self.containers[runtime_id] = parameters
        if operation == "start":
            self.tasks[runtime_id] = "running"
            self.networks.add(runtime_id)
            if self.fail == operation:
                raise RuntimeErrorBase("injected partial task failure")
            return {"private_ip": f"10.77.0.{len(self.containers) + 1}"}
        if operation == "inspect":
            return {"state": self.tasks.get(runtime_id, "stopped"), "exit_code": 137,
                    "limits_configured": True}
        if operation in {"stop", "destroy"}:
            if self.fail == operation:
                raise RuntimeErrorBase("injected cleanup failure")
            self.tasks.pop(runtime_id, None)
            self.networks.discard(runtime_id)
            if operation == "destroy":
                self.containers.pop(runtime_id, None)
                self.snapshots.discard(runtime_id)
            return {"forced": self.force}
        return {}


def spec(name="A", **kwargs):
    return RuntimeSpec(name, provider="containerd", resources=ResourceRequests(1, 256 * 1024**2, 64), **kwargs)


def provider(monkeypatch, *, fail=None, handler="runsc"):
    backend = FakeBackend(fail)
    provider = ContainerdRuntimeProvider(backend, ContainerdConfig(IMAGE, handler))
    async def ready(record):
        if backend.tasks.get(record.handle.runtime_id) != "running":
            raise RuntimeErrorBase("task not running")
    monkeypatch.setattr(provider, "_ready", ready)
    return provider, backend


@pytest.mark.parametrize("handler", ["runc", "runsc"])
async def test_native_handler_limits_and_lifecycle(monkeypatch, handler):
    p, b = provider(monkeypatch, handler=handler)
    h = await p.provision(spec())
    create = next(params for op, _, params in b.calls if op == "create")
    assert create["handler"] == HANDLERS[handler]
    assert create["image"] == IMAGE
    assert create["resources"] == {"cpu_cores": 1, "memory_bytes": 256 * 1024**2, "pids_limit": 64}
    assert not any(op == "kernel" for op, _, _ in b.calls)
    assert h.endpoint.url.startswith("http://10.77.")
    assert h.endpoint.token not in repr(h)
    assert (await p.inspect(h.runtime_id)).ready
    assert not (await p.inspect(h.runtime_id)).resource_limits_enforced
    assert await p.start(h.runtime_id) == h
    await p.stop(h.runtime_id)
    await p.stop(h.runtime_id)
    restarted = await p.start(h.runtime_id)
    assert restarted.generation == 2 and restarted.endpoint.token != h.endpoint.token
    await p.destroy(h.runtime_id)
    await p.destroy(h.runtime_id)
    await p.stop(h.runtime_id)
    assert not b.containers and not b.tasks and not b.networks and not b.snapshots
    assert (await p.inspect(h.runtime_id)).handle.state == RuntimeState.DESTROYED


@pytest.mark.parametrize("failure", ["image", "create", "start"])
async def test_partial_failure_reclaims_every_known_identity(monkeypatch, failure):
    p, b = provider(monkeypatch, fail=failure)
    with pytest.raises(RuntimeErrorBase):
        await p.provision(spec())
    assert not b.containers and not b.tasks and not b.networks and not b.snapshots
    assert all(r.handle.state == RuntimeState.DESTROYED for r in p._runtimes.values())
    assert any(op == "destroy" for op, _, _ in b.calls)


async def test_readiness_timeout_and_cancellation_cleanup(monkeypatch):
    p, b = provider(monkeypatch)
    reached = asyncio.Event()
    async def waiting(record):
        reached.set()
        await asyncio.sleep(5)
    monkeypatch.setattr(p, "_ready", waiting)
    with pytest.raises(TimeoutError):
        await p.provision(spec(startup_timeout=0.02))
    task = asyncio.create_task(p.provision(spec("B")))
    reached.clear()
    await asyncio.wait_for(reached.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not b.containers and not b.tasks and not b.networks and not b.snapshots


async def test_task_crash_and_forced_cleanup(monkeypatch):
    p, b = provider(monkeypatch)
    h = await p.provision(spec())
    b.tasks[h.runtime_id] = "stopped"
    b.force = True
    status = await p.inspect(h.runtime_id)
    assert not status.ready and not status.process_alive
    assert status.handle.state == RuntimeState.FAILED and "137" in status.failure
    assert p.measurements[-1]["forced_cleanup"]
    await p.destroy(h.runtime_id)


async def test_cleanup_failure_retains_identity_for_retry(monkeypatch):
    p, b = provider(monkeypatch)
    h = await p.provision(spec())
    b.fail = "destroy"
    with pytest.raises(RuntimeErrorBase):
        await p.destroy(h.runtime_id)
    assert h.runtime_id in p._runtimes and h.runtime_id in b.containers
    b.fail = None
    await p.close()
    assert not b.containers


async def test_supervisor_deduplicates_and_separates_container_notebooks(monkeypatch):
    p, b = provider(monkeypatch)
    class FakeClient:
        def __init__(self, url, token):
            self.url = url
        async def __aenter__(self): return self
        async def health(self): return {}
        async def close(self): pass
    monkeypatch.setattr("runtime.supervisor.GatewayClient", FakeClient)
    supervisor = RuntimeSupervisor({"containerd": p}, default_provider="containerd",
                                   default_resources=spec().resources)
    try:
        handles = await asyncio.gather(*(supervisor.ensure(spec()) for _ in range(8)))
        assert len({h.runtime_id for h in handles}) == 1
        h2 = await supervisor.ensure(spec("B"))
        assert h2.endpoint.url != handles[0].endpoint.url
        await supervisor.destroy_notebook("A")
        assert (await supervisor.inspect(h2.runtime_id)).ready
        assert (await supervisor.client_for("B")).url == h2.endpoint.url
    finally:
        await supervisor.close()
    assert not b.containers and not b.snapshots


@pytest.mark.parametrize("image,handler", [("image:latest", "runsc"), (IMAGE, "unknown")])
def test_unpinned_image_and_unknown_handler_rejected(image, handler):
    with pytest.raises(ValueError): ContainerdConfig(image, handler)


async def test_no_implicit_resources_host_mounts_or_credentials(monkeypatch, tmp_path):
    p, b = provider(monkeypatch)
    for s in [RuntimeSpec("A", provider="containerd"), spec(working_directory=tmp_path),
              spec(environment={"HOST_SECRET": "must-not-pass"})]:
        with pytest.raises(RuntimeErrorBase): await p.provision(s)
    assert not b.calls


async def test_supervisor_shutdown_reclaims_failed_unpublished_allocation(monkeypatch):
    p, b = provider(monkeypatch, fail="start")
    original = b.request
    refuse_cleanup = True
    async def failing_cleanup(operation, runtime_id, **parameters):
        if operation == "destroy" and refuse_cleanup:
            raise RuntimeErrorBase("injected cleanup failure")
        return await original(operation, runtime_id, **parameters)
    monkeypatch.setattr(b, "request", failing_cleanup)
    supervisor = RuntimeSupervisor({"containerd": p})
    with pytest.raises(RuntimeErrorBase):
        await supervisor.ensure(spec())
    assert not supervisor.handles and b.containers
    refuse_cleanup = False
    await supervisor.close()
    assert not b.containers and not b.tasks and not b.networks and not b.snapshots
