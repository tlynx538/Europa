"""Opt-in local tests: trusted source only, loopback and authenticated.

This fixture owns a test Gateway subprocess, not application kernel machinery.
Every test gets a disposable working/home directory and a separate Gateway.
"""
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import psutil
import aiohttp
import pytest
import pytest_asyncio

from kernel.gateway_client import GatewayClient

ROOT = Path(__file__).resolve().parents[1]
TIMINGS = []


def pytest_addoption(parser):
    parser.addoption("--run-local-gateway", action="store_true",
                     help="Run trusted kernels locally (development-only; no container isolation)")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-local-gateway"):
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(pytest.mark.skip(reason="requires --run-local-gateway"))


def pytest_sessionfinish(session, exitstatus):
    if TIMINGS:
        target = ROOT / "test-results"
        target.mkdir(exist_ok=True)
        (target / "gateway-timings.json").write_text(json.dumps(TIMINGS, indent=2) + "\n")


@dataclass
class LocalGateway:
    url: str
    token: str
    process: subprocess.Popen
    startup_seconds: float
    tracked: list = field(default_factory=list)
    kernel_startups: list = field(default_factory=list)

    def capture_children(self):
        if self.process.poll() is None:
            children = psutil.Process(self.process.pid).children(recursive=True)
            self.tracked.extend(children)
        return self.tracked


def alive(process):
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


@pytest.fixture
def local_gateway(tmp_path, request):
    if not request.config.getoption("--run-local-gateway"):
        pytest.skip("requires --run-local-gateway")
    if os.name != "posix":
        pytest.fail("The local process-group teardown harness requires POSIX")
    if Path(sys.prefix).resolve() != (ROOT / ".venv").resolve():
        pytest.fail("Run local integration tests with the repository .venv interpreter")
    spec = Path(sys.prefix) / "share/jupyter/kernels/python3/kernel.json"
    if not spec.exists() or Path(json.loads(spec.read_text())["argv"][0]).resolve() != Path(sys.executable).resolve():
        pytest.fail("Install the repository venv's standard python3 ipykernel kernelspec first")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    token = uuid.uuid4().hex + uuid.uuid4().hex
    for name in ["home", "work", "runtime", "config", "tmp"]:
        (tmp_path / name).mkdir()
    env = {
        "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"), "TMPDIR": str(tmp_path / "tmp"),
        "JUPYTER_RUNTIME_DIR": str(tmp_path / "runtime"),
        "JUPYTER_CONFIG_DIR": str(tmp_path / "config"),
        "JUPYTER_PATH": str(Path(sys.prefix) / "share/jupyter"),
        "KG_AUTH_TOKEN": token, "KG_PORT": str(port), "PYTHONUNBUFFERED": "1",
    }
    log = (tmp_path / "gateway.log").open("w")
    started = time.monotonic()
    process = subprocess.Popen(
        [sys.executable, "-m", "kernel_gateway",
         f"--config={ROOT / 'gateway/jupyter_kernel_gateway_config.py'}"],
        cwd=tmp_path / "work", env=env, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    gateway = LocalGateway(f"http://127.0.0.1:{port}", token, process, 0)
    fallback = False
    try:
        deadline = started + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail("Gateway exited before readiness; inspect isolated gateway.log")
            query = urllib.request.Request(f"{gateway.url}/api/kernelspecs",
                                           headers={"Authorization": f"token {token}"})
            try:
                # Do not inherit host proxy settings even for readiness checks.
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(query, timeout=0.5) as response:
                    if response.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.05)
        else:
            pytest.fail("Gateway startup exceeded 30 seconds")
        gateway.startup_seconds = time.monotonic() - started
        yield gateway
    finally:
        gateway.capture_children()
        if process.poll() is None:
            process.terminate()  # Gateway's own graceful shutdown terminates its kernels.
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                fallback = True
        survivors = [child for child in gateway.tracked if alive(child)]
        if process.poll() is None or survivors:
            fallback = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
        deadline = time.monotonic() + 5
        while any(alive(child) for child in gateway.tracked) and time.monotonic() < deadline:
            time.sleep(0.05)
        log.close()
        survivors = [child.pid for child in gateway.tracked if alive(child)]
        TIMINGS.append({"test": request.node.nodeid,
                        "gateway_startup_seconds": round(gateway.startup_seconds, 4),
                        "kernel_startup_seconds": gateway.kernel_startups,
                        "gateway_reaped": process.poll() is not None,
                        "surviving_child_pids": survivors,
                        "forced_cleanup": fallback})
        assert not survivors, f"Orphaned Gateway descendants: {survivors}"
        assert not fallback, "Gateway required forced process-group cleanup"


@pytest_asyncio.fixture
async def gateway_client(local_gateway):
    async with GatewayClient(local_gateway.url, local_gateway.token) as client:
        create = client.create_kernel

        async def timed_create(name="python3"):
            started = time.monotonic()
            model = await create(name)
            local_gateway.kernel_startups.append(round(time.monotonic() - started, 4))
            return model
        client.create_kernel = timed_create
        try:
            yield client
        finally:
            local_gateway.capture_children()
    # Verify Europa's teardown before the fixture stops Gateway itself.
    if local_gateway.process.poll() is None:
        async with aiohttp.ClientSession(
            headers={"Authorization": f"token {local_gateway.token}"},
            timeout=aiohttp.ClientTimeout(total=5),
        ) as http:
            async with http.get(f"{local_gateway.url}/api/kernels") as response:
                assert response.status == 200
                assert await response.json() == []
        assert not any(alive(child) for child in local_gateway.tracked)
