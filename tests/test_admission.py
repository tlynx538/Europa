import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from runtime.admission import (AdmissionController, AdmissionPolicy, AdmissionRejected,
                               HostCapacity, ResourceProfiles)
from runtime.admission_config import configured_admission
from runtime.capacity import LinuxCapacityDetector, SingleWriterGuard, cpuset_size
from runtime.contracts import (GatewayEndpoint, ResourceRequests, RuntimeErrorBase, RuntimeHandle,
                               RuntimeSpec, RuntimeState, RuntimeStatus)
from runtime.supervisor import RuntimeSupervisor

MiB = 1024**2


class Detector:
    def __init__(self, memory=4096 * MiB, available=None, cpu=4):
        self.value = HostCapacity(memory, memory if available is None else available, cpu)
        self.fail = False

    def detect(self):
        if self.fail:
            raise OSError("unreadable host")
        return self.value


def controller(detector=None, **policy):
    return AdmissionController(detector or Detector(), policy=AdmissionPolicy(
        **({"memory_reserve": 0, "cpu_reserve": 0} | policy)))


def spec(name="A", **kwargs):
    return RuntimeSpec(name, provider="containerd", **kwargs)


@pytest.mark.parametrize("values", [
    (True, MiB, 10), (float("nan"), MiB, 10), (float("inf"), MiB, 10),
    (0, MiB, 10), (-1, MiB, 10), (1025, MiB, 10), (.0001, MiB, 10),
    (1, True, 10), (1, 2.5, 10), (1, 0, 10), (1, MiB, None),
    (1, MiB, False), (1, MiB, 2.5), (None, MiB, 10), (1, 2**63, 10),
])
def test_invalid_complete_profiles(values):
    with pytest.raises(ValueError):
        ResourceProfiles({"bad": ResourceRequests(*values)}, default="bad")


def test_profile_identifiers_defaults_and_resolution():
    profiles = ResourceProfiles()
    name, resources = profiles.resolve(spec())
    assert name == "standard" and resources == ResourceRequests(.5, 512 * MiB, 128)
    assert profiles.resolve(spec(resources=ResourceRequests(1, MiB, 1)))[0] is None
    for mapping, default in [({}, "standard"), ({" ": resources}, " "), ({"ok": resources}, "missing")]:
        with pytest.raises(ValueError):
            ResourceProfiles(mapping, default)
    with pytest.raises(AdmissionRejected, match="Unknown"):
        profiles.resolve(spec(resource_profile="unknown"))
    with pytest.raises(AdmissionRejected, match="not both"):
        profiles.resolve(spec(resource_profile="standard", resources=resources))


@pytest.mark.parametrize("detector,policy,message", [
    (Detector(memory=1024 * MiB, available=400 * MiB), {}, "memory"),
    (Detector(cpu=.25), {}, "CPU"),
    (Detector(memory=1024 * MiB), {"memory_reserve": 600 * MiB}, "memory"),
    (Detector(cpu=2), {"cpu_reserve": 1.75}, "CPU"),
    (Detector(), {"memory_ceiling": 400 * MiB}, "memory"),
    (Detector(), {"cpu_ceiling": .25}, "CPU"),
])
async def test_insufficient_capacity_and_host_reserve(detector, policy, message):
    c = controller(detector, **policy)
    with pytest.raises(AdmissionRejected, match=message):
        await c.reserve("A", spec())
    metrics = await c.metrics()
    assert metrics["reservation_count"] == 0 and metrics["admission_rejections"] == 1


async def test_concurrent_admission_cannot_overcommit():
    c = controller(Detector(memory=512 * MiB))
    results = await asyncio.gather(*(c.reserve(str(n), spec(str(n))) for n in range(20)), return_exceptions=True)
    assert sum(not isinstance(r, BaseException) for r in results) == 1
    assert (await c.metrics())["reserved"]["memory_bytes"] == 512 * MiB
    assert c.rejections == 19


async def test_unknown_capacity_fails_closed_and_metrics_preserve_reservations():
    d = Detector()
    c = controller(d)
    await c.reserve("A", spec())
    d.fail = True
    with pytest.raises(AdmissionRejected, match="reliably"):
        await c.reserve("B", spec("B"))
    metrics = await c.metrics()
    assert metrics["reservation_count"] == 1 and metrics["available_admission"] is None
    await c.release("A")
    assert (await c.metrics())["reservation_count"] == 0


async def test_duplicate_reservation_and_observed_usage_are_not_free_capacity():
    d = Detector(memory=2048 * MiB)
    c = controller(d, memory_reserve=256 * MiB)
    await c.reserve("A", spec())
    with pytest.raises(AdmissionRejected, match="already"):
        await c.reserve("A", spec())
    d.value = HostCapacity(2048 * MiB, 700 * MiB, 4)
    with pytest.raises(AdmissionRejected, match="memory"):
        await c.reserve("B", spec("B"))
    assert (await c.metrics())["reserved"]["memory_bytes"] == 512 * MiB


class Client:
    def __init__(self, *args): pass
    async def __aenter__(self): return self
    async def health(self): return {}
    async def close(self): pass


class Provider:
    def __init__(self):
        self.records = {}
        self.starts = 0
        self.started = asyncio.Event()
        self.pause_start = False
        self.start_release = asyncio.Event()
        self.destroy_started = asyncio.Event()
        self.pause_destroy = False
        self.destroy_release = asyncio.Event()
        self.fail_start = self.fail_destroy = False
        self.verified = True

    async def provision(self, s):
        self.starts += 1
        h = RuntimeHandle(s.allocation_id, "containerd", s.notebook_id,
                          GatewayEndpoint("http://10.77.0.2:8888", "test-only"), 1, RuntimeState.READY, "now")
        self.records[h.runtime_id] = h
        self.started.set()
        if self.pause_start:
            await self.start_release.wait()
        if self.fail_start:
            raise RuntimeErrorBase("injected startup failure")
        return h

    async def inspect(self, identity):
        h = self.records[identity]
        return RuntimeStatus(h, h.state == RuntimeState.READY, h.state == RuntimeState.READY,
                             resource_limits_enforced=self.verified)

    async def destroy(self, identity):
        self.destroy_started.set()
        if self.pause_destroy:
            await self.destroy_release.wait()
        if self.fail_destroy:
            raise RuntimeErrorBase("injected teardown failure")
        self.records.pop(identity, None)

    async def close(self):
        for identity in list(self.records):
            await self.destroy(identity)


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr("runtime.supervisor.GatewayClient", Client)
    p = Provider()
    c = controller()
    s = RuntimeSupervisor({"containerd": p}, default_provider="containerd", admission=c)
    return s, p, c


async def test_duplicate_and_independent_notebooks_release_only_on_destroy(setup):
    s, p, c = setup
    handles = await asyncio.gather(*(s.ensure(spec()) for _ in range(20)))
    assert p.starts == 1 and len({h.runtime_id for h in handles}) == 1
    await s.ensure(spec("B"))
    assert (await c.metrics())["active_notebook_runtime_count"] == 2
    await s.destroy_notebook("A")
    assert (await c.metrics())["reservation_count"] == 1
    assert (await s.status_for("B"))["ready"]
    await s.close()
    assert (await c.metrics())["reservation_count"] == 0 and not p.records


async def test_profile_change_for_existing_notebook_is_rejected(setup):
    s, p, c = setup
    await s.ensure(spec())
    with pytest.raises(AdmissionRejected, match="different"):
        await s.ensure(spec(resource_profile="other"))
    assert p.starts == 1
    await s.close()


@pytest.mark.parametrize("kind", ["startup", "enforcement", "health"])
async def test_failed_startup_or_unverified_limits_roll_back(setup, kind, monkeypatch):
    s, p, c = setup
    p.fail_start = kind == "startup"
    p.verified = kind != "enforcement"
    if kind == "health":
        async def fail(self): raise RuntimeErrorBase("injected Gateway readiness failure")
        monkeypatch.setattr(Client, "health", fail)
    with pytest.raises(RuntimeErrorBase):
        await s.ensure(spec())
    assert not p.records and not s.handles
    assert (await c.metrics())["reservation_count"] == 0


async def test_failed_unpublished_cleanup_retains_capacity_and_identity(setup):
    s, p, c = setup
    p.fail_start = p.fail_destroy = True
    with pytest.raises(RuntimeErrorBase):
        await s.ensure(spec())
    assert p.records and not s.handles
    assert (await c.metrics())["reservation_count"] == 1
    assert (await c.metrics())["reservation_cleanup_failures"] == 1
    with pytest.raises(AdmissionRejected, match="pending"):
        await s.ensure(spec())
    p.fail_destroy = False
    await s.destroy_notebook("A")
    assert not p.records and (await c.metrics())["reservation_count"] == 0


async def test_cancelled_startup_holds_reservation_until_cleanup_finishes(setup):
    s, p, c = setup
    p.pause_start = p.pause_destroy = True
    task = asyncio.create_task(s.ensure(spec()))
    await p.started.wait()
    task.cancel()
    await p.destroy_started.wait()
    task.cancel()  # repeated cancellation cannot cancel the ownership cleanup task
    assert (await c.metrics())["reservation_count"] == 1 and p.records
    p.destroy_release.set()
    with pytest.raises(asyncio.CancelledError): await task
    assert not p.records and (await c.metrics())["reservation_count"] == 0


async def test_cancelled_startup_with_failed_cleanup_keeps_reservation(setup):
    s, p, c = setup
    p.pause_start = p.fail_destroy = True
    task = asyncio.create_task(s.ensure(spec()))
    await p.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert (await c.metrics())["reservation_count"] == 1 and p.records
    p.fail_destroy = False
    await s.close()
    assert (await c.metrics())["reservation_count"] == 0


async def test_crash_and_failed_destroy_never_free_capacity(setup):
    s, p, c = setup
    h = await s.ensure(spec())
    p.records[h.runtime_id] = replace(h, state=RuntimeState.FAILED)
    assert not (await s.inspect(h.runtime_id)).ready
    assert (await c.metrics())["reservation_count"] == 1
    p.fail_destroy = True
    with pytest.raises(RuntimeErrorBase): await s.destroy_notebook("A")
    assert (await c.metrics())["reservation_count"] == 1
    p.fail_destroy = False
    await s.destroy_notebook("A")
    assert (await c.metrics())["reservation_count"] == 0


async def test_reservation_held_during_shutdown_and_cancellation(setup):
    s, p, c = setup
    await s.ensure(spec())
    p.pause_destroy = True
    task = asyncio.create_task(s.destroy_notebook("A"))
    await p.destroy_started.wait()
    task.cancel()
    assert (await c.metrics())["reservation_count"] == 1
    p.destroy_release.set()
    with pytest.raises(asyncio.CancelledError): await task
    assert not p.records and (await c.metrics())["reservation_count"] == 0


def fake_linux(tmp_path):
    proc, cg = tmp_path / "proc", tmp_path / "cgroup"
    (proc / "self").mkdir(parents=True)
    (cg / "group/leaf").mkdir(parents=True)
    (proc / "meminfo").write_text("MemTotal: 4194304 kB\nMemAvailable: 3145728 kB\n")
    (proc / "self/cgroup").write_text("0::/group/leaf\n")
    (proc / "self/mountinfo").write_text(f"1 0 0:1 / {cg} rw - cgroup2 cgroup rw\n")
    for d in [cg / "group", cg / "group/leaf"]:
        (d / "memory.max").write_text("max")
        (d / "memory.current").write_text("0")
        (d / "cpu.max").write_text("max 100000")
    return proc, cg


def test_linux_ancestor_limits_and_effective_cpus(tmp_path):
    proc, cg = fake_linux(tmp_path)
    (cg / "group/memory.max").write_text(str(1024 * MiB))
    (cg / "group/memory.current").write_text(str(256 * MiB))
    (cg / "group/cpu.max").write_text("75000 100000")
    (cg / "group/leaf/cpuset.cpus.effective").write_text("1-2,4")
    result = LinuxCapacityDetector(proc, lambda: {0, 1, 2, 3}).detect()
    assert result == HostCapacity(1024 * MiB, 768 * MiB, .75)
    assert cpuset_size("0-2,4") == 4


@pytest.mark.parametrize("fault", ["meminfo", "membership", "hidden", "missing", "cpu", "usage"])
def test_linux_unknown_or_inconsistent_capacity_rejected(tmp_path, fault):
    proc, cg = fake_linux(tmp_path)
    if fault == "meminfo": (proc / "meminfo").write_text("MemTotal: 100 kB\n")
    if fault == "membership": (proc / "self/cgroup").write_text("2:memory:/legacy\n")
    if fault == "hidden": (proc / "self/mountinfo").write_text(f"1 0 0:1 /group {cg} rw - cgroup2 cgroup rw\n")
    if fault == "missing": (cg / "group/memory.max").unlink()
    if fault == "cpu": (cg / "group/cpu.max").write_text("1000 0")
    if fault == "usage":
        (cg / "group/memory.max").write_text("10000")
        (cg / "group/memory.current").write_text("invalid")
    with pytest.raises((ValueError, KeyError)):
        LinuxCapacityDetector(proc, lambda: {0, 1}).detect()


def test_single_writer_guard_excludes_a_second_controller(tmp_path):
    first = SingleWriterGuard(tmp_path / "admission.lock")
    second = SingleWriterGuard(tmp_path / "admission.lock")
    first.acquire()
    try:
        with pytest.raises(AdmissionRejected): second.acquire()
    finally:
        first.close()
    second.acquire()
    second.close()


def test_configuration_profiles_and_legacy_compatibility(monkeypatch):
    for key in ("CPU_CORES", "MEMORY_BYTES", "PIDS_LIMIT"):
        monkeypatch.delenv("EUROPA_CONTAINERD_" + key, raising=False)
    monkeypatch.delenv("EUROPA_RESOURCE_PROFILES", raising=False)
    c, _ = configured_admission("/test/containerd.sock")
    assert c.profiles.resolve(spec())[1] == ResourceRequests(.5, 512 * MiB, 128)
    monkeypatch.setenv("EUROPA_CONTAINERD_CPU_CORES", "1")
    with pytest.raises(ValueError, match="together"): configured_admission("/test/socket")
    monkeypatch.setenv("EUROPA_RESOURCE_PROFILES", '{"small":{"cpu_cores":0.25,"memory_bytes":268435456,"pids_limit":64}}')
    monkeypatch.setenv("EUROPA_RESOURCE_DEFAULT_PROFILE", "small")
    c, _ = configured_admission("/test/socket")
    assert c.profiles.resolve(spec())[0] == "small"


async def test_existing_explicit_limits_cannot_be_changed(setup):
    supervisor, provider, admission = setup
    resources = ResourceRequests(.5, 512 * MiB, 128)
    await supervisor.ensure(spec(resources=resources))
    with pytest.raises(AdmissionRejected, match='different'):
        await supervisor.ensure(spec(resources=ResourceRequests(.25, 512 * MiB, 128)))
    assert (await admission.reservation('A'))['resources']['cpu_cores'] == .5


async def test_unknown_capacity_metrics_keep_reserved_amounts():
    detector = Detector()
    admission = controller(detector)
    await admission.reserve('A', spec())
    await admission.activate('A')
    detector.fail = True
    metrics = await admission.metrics()
    assert metrics['allocatable'] is None and metrics['available_admission'] is None
    assert metrics['reserved']['memory_bytes'] == 512 * MiB
    assert metrics['active_notebook_runtime_count'] == 1


async def test_managed_namespace_guard_and_allocation_identity(monkeypatch):
    from test_containerd_provider import provider, spec as container_spec
    runtime, backend = provider(monkeypatch)
    identity = 'europa-' + '1' * 32
    handle = await runtime.provision(container_spec(allocation_id=identity))
    assert handle.runtime_id == identity
    with pytest.raises(RuntimeErrorBase, match='reconciliation'):
        await runtime.assert_empty_namespace()  # Unknown/malformed inventory is unsafe too.
    await runtime.close()
