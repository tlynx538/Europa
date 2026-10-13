"""Trusted POSIX development runtimes. Gateway owns all kernel operations."""
import asyncio
import os
import secrets
import shutil
import signal
import socket
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import psutil

from runtime.contracts import (
    GatewayEndpoint, RuntimeErrorBase, RuntimeHandle, RuntimeSpec, RuntimeState, RuntimeStatus,
)

CONFIG = Path(__file__).resolve().parents[3] / "gateway/jupyter_kernel_gateway_config.py"


def now():
    return datetime.now(timezone.utc).isoformat()


def alive(process: psutil.Process):
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


@dataclass
class _Runtime:
    spec: RuntimeSpec
    handle: RuntimeHandle
    directory: Path
    process: asyncio.subprocess.Process | None = None
    children: set[psutil.Process] = field(default_factory=set)
    observer: asyncio.Task | None = None
    failure: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class LocalRuntimeProvider:
    def __init__(self, *, config_path: Path = CONFIG, root: Path | None = None,
                 stop_timeout: float = 5):
        if os.name != "posix":
            raise RuntimeErrorBase("Local runtime provider requires POSIX process groups")
        self.config_path = config_path.resolve()
        self.root = root.resolve() if root else None
        if not 0 < stop_timeout <= 60:
            raise ValueError("Stop timeout must be in (0, 60]")
        self.stop_timeout = stop_timeout
        self._runtimes: dict[str, _Runtime] = {}
        self.measurements: list[dict] = []

    async def provision(self, spec: RuntimeSpec):
        if spec.provider != "local":
            raise RuntimeErrorBase("Local provider cannot provision the requested provider")
        if not self.config_path.is_file():
            raise RuntimeErrorBase("Gateway configuration file is missing")
        if spec.working_directory is not None and not spec.working_directory.is_dir():
            raise RuntimeErrorBase("Working directory must already exist")
        # Runtime environment is a small explicit allowlist, not host inheritance.
        if set(spec.environment) - {"LANG", "LC_ALL"}:
            raise RuntimeErrorBase("Local runtime environment accepts only locale overrides")
        spec = replace(spec, environment=dict(spec.environment), working_directory=(
            spec.working_directory.resolve() if spec.working_directory else None))
        directory = Path(tempfile.mkdtemp(prefix="europa-runtime-", dir=self.root))
        for name in ("home", "work", "runtime", "config", "tmp"):
            (directory / name).mkdir()
        runtime_id = uuid.uuid4().hex
        handle = RuntimeHandle(runtime_id, "local", spec.notebook_id, None, 0,
                               RuntimeState.STOPPED, now())
        record = _Runtime(spec, handle, directory)
        self._runtimes[runtime_id] = record
        try:
            return await self.start(runtime_id)
        except BaseException:
            await self._finish_cleanup(self.destroy(runtime_id))
            raise

    def _record(self, runtime_id):
        try:
            return self._runtimes[runtime_id]
        except KeyError as exc:
            raise RuntimeErrorBase("Unknown runtime") from exc

    @staticmethod
    def _port():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def _capture(self, record):
        if record.process is not None:
            try:
                record.children.update(psutil.Process(record.process.pid).children(recursive=True))
            except psutil.NoSuchProcess:
                pass
            # A dead Gateway's children may have been reparented. Its dedicated
            # process group still identifies them; never scan/kill by name.
            for process in psutil.process_iter(["pid"]) if record.process.returncode is not None else ():
                try:
                    if os.getpgid(process.pid) == record.process.pid:
                        record.children.add(process)
                except (ProcessLookupError, PermissionError, psutil.NoSuchProcess):
                    pass

    async def _observe(self, record):
        # Track descendants while ancestry still exists. Jupyter starts kernels
        # in separate sessions, so Gateway's process group alone is insufficient.
        while record.process and record.process.returncode is None:
            self._capture(record)
            record.children = {child for child in record.children if alive(child)}
            await asyncio.sleep(0.05)

    def processes(self, runtime_id: str) -> tuple[psutil.Process, ...]:
        """Local diagnostics only; never part of RuntimeHandle or GatewayClient."""
        record = self._record(runtime_id)
        self._capture(record)
        processes = list(record.children)
        if record.process and record.process.returncode is None:
            processes.append(psutil.Process(record.process.pid))
        return tuple(processes)

    async def _ready(self, record):
        endpoint = record.handle.endpoint
        async with aiohttp.ClientSession(
            headers={"Authorization": f"token {endpoint.token}"},
            timeout=aiohttp.ClientTimeout(total=0.5), trust_env=False,
        ) as http:
            while True:
                if record.process.returncode is not None:
                    raise RuntimeErrorBase("Gateway exited before readiness")
                try:
                    async with http.get(f"{endpoint.url}/api/kernelspecs", allow_redirects=False) as response:
                        if response.status == 200 and "python3" in (await response.json())["kernelspecs"]:
                            return
                except (aiohttp.ClientError, TimeoutError, ValueError, KeyError):
                    pass
                await asyncio.sleep(0.05)

    async def start(self, runtime_id):
        record = self._record(runtime_id)
        async with record.lock:
            if record.handle.state == RuntimeState.DESTROYED:
                raise RuntimeErrorBase("Runtime was destroyed")
            if record.process and record.process.returncode is None:
                if record.handle.state == RuntimeState.READY:
                    return record.handle
                raise RuntimeErrorBase("Runtime process has not been cleaned up")
            await self._stop(record)
            started = time.monotonic()
            record.failure = None
            record.children.clear()
            record.handle = replace(record.handle, state=RuntimeState.STARTING,
                                    generation=record.handle.generation + 1, started_at=now())
            env = {
                "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
                "HOME": str(record.directory / "home"), "TMPDIR": str(record.directory / "tmp"),
                "JUPYTER_RUNTIME_DIR": str(record.directory / "runtime"),
                "JUPYTER_CONFIG_DIR": str(record.directory / "config"),
                "JUPYTER_PATH": str(Path(sys.prefix) / "share/jupyter"),
                "PYTHONUNBUFFERED": "1", **record.spec.environment,
                "TRAITLETS_APPLICATION_RAISE_CONFIG_FILE_ERROR": "1",
            }
            try:
                async with asyncio.timeout(record.spec.startup_timeout):
                    # Retry bind races only after fully reaping the failed attempt.
                    for attempt in range(3):
                        port = self._port()
                        token = secrets.token_urlsafe(32)
                        record.handle = replace(record.handle, endpoint=GatewayEndpoint(
                            f"http://127.0.0.1:{port}", token))
                        env.update(KG_PORT=str(port), KG_AUTH_TOKEN=token)
                        with (record.directory / "gateway.log").open("w") as log:
                            spawning = asyncio.create_task(asyncio.create_subprocess_exec(
                                sys.executable, "-m", "kernel_gateway", f"--config={self.config_path}",
                                "--KernelGatewayApp.ip=127.0.0.1",
                                f"--KernelGatewayApp.port={port}", "--KernelGatewayApp.port_retries=0",
                                "--KernelGatewayApp.api=kernel_gateway.jupyter_websocket",
                                cwd=record.spec.working_directory or record.directory / "work",
                                env=env, stdout=log, stderr=asyncio.subprocess.STDOUT,
                                start_new_session=True,
                            ))
                            try:
                                record.process = await asyncio.shield(spawning)
                            except asyncio.CancelledError:
                                record.process = await spawning
                                raise
                        spawned = time.monotonic()
                        record.observer = asyncio.create_task(self._observe(record))
                        try:
                            await self._ready(record)
                            break
                        except RuntimeErrorBase:
                            if "Address already in use" not in (record.directory / "gateway.log").read_text() or attempt == 2:
                                raise
                            await self._stop(record)
                    record.handle = replace(record.handle, state=RuntimeState.READY)
                    self.measurements.append({"runtime_id": runtime_id, "generation": record.handle.generation,
                        "spawn_seconds": spawned - started, "readiness_seconds": time.monotonic() - started})
                    return record.handle
            except BaseException as exc:
                record.failure = "Gateway startup timed out" if isinstance(exc, TimeoutError) else "Gateway startup failed"
                await self._finish_cleanup(self._stop(record))
                record.handle = replace(record.handle, state=RuntimeState.FAILED, endpoint=None)
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise RuntimeErrorBase(record.failure) from exc

    async def inspect(self, runtime_id):
        record = self._record(runtime_id)
        async with record.lock:
            process_alive = record.process is not None and record.process.returncode is None
            ready = False
            if record.handle.state == RuntimeState.READY:
                if not process_alive:
                    record.failure = "Gateway process exited unexpectedly"
                    await self._stop(record)
                    record.handle = replace(record.handle, state=RuntimeState.FAILED, endpoint=None)
                else:
                    try:
                        async with asyncio.timeout(1):
                            await self._ready(record)
                            ready = True
                            record.failure = None
                    except (TimeoutError, RuntimeErrorBase):
                        record.failure = "Gateway is not ready"
            return RuntimeStatus(record.handle, process_alive, ready, record.failure)

    @staticmethod
    async def _finish_cleanup(coroutine):
        task = asyncio.create_task(coroutine)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def _stop(self, record):
        if not record.process:
            return
        started = time.monotonic()
        self._capture(record)
        if record.observer:
            record.observer.cancel()
            await asyncio.gather(record.observer, return_exceptions=True)
            record.observer = None
        forced = False
        if record.process.returncode is None:
            try:
                record.process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(record.process.wait(), self.stop_timeout)
            except TimeoutError:
                forced = True
        if record.process.returncode is None or any(alive(child) for child in record.children):
            forced = True
            try:
                os.killpg(record.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await asyncio.wait_for(record.process.wait(), self.stop_timeout)
            for child in record.children:
                if alive(child):
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
        deadline = time.monotonic() + self.stop_timeout
        while any(alive(child) for child in record.children) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        survivors = [child.pid for child in record.children if alive(child)]
        self.measurements.append({"runtime_id": record.handle.runtime_id,
            "teardown_seconds": time.monotonic() - started, "forced_cleanup": forced,
            "unreclaimed_processes": survivors})
        if survivors:
            raise RuntimeErrorBase("Runtime descendants could not be reclaimed")
        record.process = None

    async def stop(self, runtime_id):
        record = self._runtimes.get(runtime_id)
        if record is None:
            return
        async with record.lock:
            async def stopping():
                await self._stop(record)
                if record.handle.state != RuntimeState.DESTROYED:
                    record.handle = replace(record.handle, state=RuntimeState.STOPPED, endpoint=None)
            await self._finish_cleanup(stopping())

    async def destroy(self, runtime_id):
        record = self._runtimes.get(runtime_id)
        if record is None:
            return
        async with record.lock:
            async def destroying():
                await self._stop(record)
                if record.handle.state != RuntimeState.DESTROYED:
                    await asyncio.to_thread(shutil.rmtree, record.directory)
                    record.handle = replace(record.handle, state=RuntimeState.DESTROYED, endpoint=None)
            await self._finish_cleanup(destroying())

    async def close(self):
        failures = []
        for runtime_id in tuple(self._runtimes):
            try:
                await self.destroy(runtime_id)
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise RuntimeErrorBase("One or more local runtimes failed cleanup") from failures[0]
