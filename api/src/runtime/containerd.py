"""One private Gateway container per runtime, using a mockable SDK backend."""
import asyncio
import ipaddress
import math
import re
import secrets
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone

import aiohttp

from runtime.containerd_backend import ContainerdBackend
from runtime.admission import validate_resources
from runtime.contracts import (
    GatewayEndpoint, ResourceRequests, RuntimeErrorBase, RuntimeHandle, RuntimeSpec,
    RuntimeState, RuntimeStatus,
)

HANDLERS = {"runc": "io.containerd.runc.v2", "runsc": "io.containerd.runsc.v1"}


@dataclass(frozen=True)
class ContainerdConfig:
    image: str
    handler: str = "runc"
    stop_timeout: float = 5

    def __post_init__(self):
        if not re.fullmatch(r"[^\s@]+@sha256:[a-f0-9]{64}", self.image):
            raise ValueError("Container image must be pinned by sha256 digest")
        if self.handler not in HANDLERS or not 0 < self.stop_timeout <= 60:
            raise ValueError("Handler must be runc/runsc and stop timeout in (0, 60]")


@dataclass
class _Container:
    spec: RuntimeSpec
    handle: RuntimeHandle
    failure: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ContainerdRuntimeProvider:
    def __init__(self, backend: ContainerdBackend, config: ContainerdConfig):
        self.backend, self.config = backend, config
        self._runtimes: dict[str, _Container] = {}
        self.measurements: list[dict] = []

    @staticmethod
    def _validate_resources(resources: ResourceRequests | None):
        try:
            validate_resources(resources)
        except ValueError as exc:
            raise RuntimeErrorBase(str(exc)) from exc
        if (resources is None or resources.cpu_cores is None or resources.memory_bytes is None
                or resources.pids_limit is None or not math.isfinite(resources.cpu_cores)):
            raise RuntimeErrorBase("Container runtimes require explicit CPU, memory, and PID limits")
        if not 0.001 <= resources.cpu_cores <= 1024:
            raise RuntimeErrorBase("CPU request must be in [0.001, 1024]")

    async def provision(self, spec: RuntimeSpec):
        if spec.provider != "containerd":
            raise RuntimeErrorBase("Containerd provider requires containerd spec")
        self._validate_resources(spec.resources)
        if spec.working_directory is not None:
            raise RuntimeErrorBase("Host working-directory mounts are not supported")
        if set(spec.environment) - {"LANG", "LC_ALL"}:
            raise RuntimeErrorBase("Container environment accepts only locale overrides")
        runtime_id = spec.allocation_id or f"europa-{uuid.uuid4().hex}"
        if not re.fullmatch(r"europa-[a-f0-9]{32}", runtime_id) or runtime_id in self._runtimes:
            raise RuntimeErrorBase("Invalid or duplicate runtime allocation identity")
        handle = RuntimeHandle(runtime_id, "containerd", spec.notebook_id, None, 0,
                               RuntimeState.STOPPED, datetime.now(timezone.utc).isoformat())
        record = _Container(replace(spec, environment=dict(spec.environment)), handle)
        # Register before any side effect. IDs determine task/container/snapshot/network cleanup.
        self._runtimes[runtime_id] = record
        try:
            async with asyncio.timeout(spec.startup_timeout):
                started = time.monotonic()
                image = await self.backend.request("image", runtime_id, image=self.config.image)
                self.measurements.append({"runtime_id": runtime_id, "image_seconds": time.monotonic() - started,
                    "image_cached": image.get("cached", False),
                    "image_availability_seconds": image.get("availability_seconds"),
                    "image_unpack_seconds": image.get("unpack_seconds")})
                started = time.monotonic()
                await self.backend.request("create", runtime_id, image=self.config.image,
                    handler=HANDLERS[self.config.handler], notebook_id=spec.notebook_id,
                    resources=asdict(spec.resources))
                self.measurements.append({"runtime_id": runtime_id, "provision_seconds": time.monotonic() - started})
                return await self._start(record)
        except BaseException:
            await self._cleanup(self.destroy(runtime_id))
            raise

    async def assert_empty_namespace(self):
        inventory = await self.backend.request("managed_inventory", "europa-" + "0" * 32)
        expected = {"containers": 0, "tasks": 0, "snapshots": 0, "state_directories": 0}
        if inventory != expected:
            raise RuntimeErrorBase("Existing containerd allocations require operator reconciliation before admission")

    async def _ready(self, record):
        endpoint = record.handle.endpoint
        async with aiohttp.ClientSession(headers={"Authorization": f"token {endpoint.token}"},
            timeout=aiohttp.ClientTimeout(total=0.5), trust_env=False) as http:
            while True:
                observation = await self.backend.request("inspect", record.handle.runtime_id)
                if observation.get("state") != "running":
                    raise RuntimeErrorBase("Container task exited before Gateway readiness")
                try:
                    async with http.get(f"{endpoint.url}/api/kernelspecs", allow_redirects=False) as response:
                        if response.status == 200 and "python3" in (await response.json())["kernelspecs"]:
                            return
                except (aiohttp.ClientError, TimeoutError, KeyError, ValueError):
                    pass
                await asyncio.sleep(0.05)

    async def _start(self, record):
        runtime_id = record.handle.runtime_id
        token = secrets.token_urlsafe(32)
        record.handle = replace(record.handle, state=RuntimeState.STARTING,
            generation=record.handle.generation + 1, started_at=datetime.now(timezone.utc).isoformat())
        started = time.monotonic()
        response = await self.backend.request("start", runtime_id, token=token,
                                             environment=dict(record.spec.environment))
        address = ipaddress.ip_address(response["private_ip"])
        if address.version != 4 or not address.is_private or address.is_loopback or address.is_unspecified:
            raise RuntimeErrorBase("Backend did not return a private IPv4 endpoint")
        record.handle = replace(record.handle, endpoint=GatewayEndpoint(f"http://{address}:8888", token))
        self.measurements.append({"runtime_id": runtime_id, "task_start_seconds": time.monotonic() - started})
        started = time.monotonic()
        await self._ready(record)
        record.handle = replace(record.handle, state=RuntimeState.READY)
        record.failure = None
        self.measurements.append({"runtime_id": runtime_id, "gateway_ready_seconds": time.monotonic() - started})
        return record.handle

    async def start(self, runtime_id):
        record = self._record(runtime_id)
        async with record.lock:
            if record.handle.state == RuntimeState.DESTROYED:
                raise RuntimeErrorBase("Runtime was destroyed")
            if record.handle.state == RuntimeState.READY:
                status = await self._inspect(record)
                if status.ready:
                    return record.handle
                raise RuntimeErrorBase("Runtime is not ready")
            try:
                async with asyncio.timeout(record.spec.startup_timeout):
                    return await self._start(record)
            except BaseException:
                await self._cleanup(self._stop(record))
                record.failure = "Container startup failed"
                record.handle = replace(record.handle, state=RuntimeState.FAILED, endpoint=None)
                raise

    def _record(self, runtime_id):
        try:
            return self._runtimes[runtime_id]
        except KeyError as exc:
            raise RuntimeErrorBase("Unknown container runtime") from exc

    async def _inspect(self, record):
        observation = await self.backend.request("inspect", record.handle.runtime_id)
        process_alive = observation.get("state") == "running"
        ready = False
        if record.handle.state == RuntimeState.READY:
            if not process_alive:
                record.failure = f"Container task exited (code {observation.get('exit_code')})"
                await self._cleanup(self._stop(record))
                record.handle = replace(record.handle, state=RuntimeState.FAILED, endpoint=None)
            else:
                try:
                    async with asyncio.timeout(1):
                        await self._ready(record)
                    ready = True
                except (TimeoutError, RuntimeErrorBase):
                    record.failure = "Gateway is not ready"
        # Configured OCI limits are not proof of host cgroup enforcement.
        return RuntimeStatus(record.handle, process_alive, ready, record.failure,
                             resource_limits_enforced=observation.get("limits_verified", False))

    async def inspect(self, runtime_id):
        record = self._record(runtime_id)
        async with record.lock:
            if record.handle.state == RuntimeState.DESTROYED:
                return RuntimeStatus(record.handle, False, False)
            return await self._inspect(record)

    @staticmethod
    async def _cleanup(coroutine):
        task = asyncio.create_task(coroutine)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def _stop(self, record):
        started = time.monotonic()
        result = await self.backend.request("stop", record.handle.runtime_id,
                                            grace_seconds=self.config.stop_timeout)
        record.handle = replace(record.handle, state=RuntimeState.STOPPED, endpoint=None)
        self.measurements.append({"runtime_id": record.handle.runtime_id,
            "stop_seconds": time.monotonic() - started, "forced_cleanup": result.get("forced", False)})

    async def stop(self, runtime_id):
        record = self._runtimes.get(runtime_id)
        if record is None:
            return
        async with record.lock:
            if record.handle.state not in {RuntimeState.STOPPED, RuntimeState.DESTROYED}:
                await self._cleanup(self._stop(record))

    async def destroy(self, runtime_id):
        record = self._runtimes.get(runtime_id)
        if record is None:
            return
        async with record.lock:
            if record.handle.state == RuntimeState.DESTROYED:
                return
            async def destroy_resources():
                started = time.monotonic()
                await self.backend.request("destroy", runtime_id, grace_seconds=self.config.stop_timeout)
                record.handle = replace(record.handle, state=RuntimeState.DESTROYED, endpoint=None)
                self.measurements.append({"runtime_id": runtime_id, "cleanup_seconds": time.monotonic() - started})
            await self._cleanup(destroy_resources())

    async def close(self):
        failures = []
        for runtime_id in tuple(self._runtimes):
            try:
                await self.destroy(runtime_id)
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise RuntimeErrorBase("Container runtime cleanup failed; ownership retained") from failures[0]
