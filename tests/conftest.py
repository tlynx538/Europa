"""Opt-in local tests: trusted source only, loopback and authenticated.

This fixture owns a test Gateway subprocess, not application kernel machinery.
Every test gets a disposable working/home directory and a separate Gateway.
"""
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil
import aiohttp
import pytest
import pytest_asyncio

from kernel.gateway_client import GatewayClient
from runtime.contracts import RuntimeSpec
from runtime.local import LocalRuntimeProvider

ROOT = Path(__file__).resolve().parents[1]
TIMINGS = []


def pytest_addoption(parser):
    parser.addoption("--run-local-gateway", action="store_true",
                     help="Run trusted kernels locally (development-only; no container isolation)")
    parser.addoption("--run-containerd", action="store_true",
                     help="Use operator-approved disposable Linux containerd/CNI resources")


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
    process: psutil.Process
    provider: LocalRuntimeProvider
    runtime_id: str
    startup_seconds: float
    tracked: list = field(default_factory=list)
    kernel_startups: list = field(default_factory=list)

    def capture_children(self):
        if alive(self.process):
            children = psutil.Process(self.process.pid).children(recursive=True)
            self.tracked.extend(children)
        return self.tracked


def alive(process):
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


@pytest_asyncio.fixture
async def local_gateway(tmp_path, request):
    if not request.config.getoption("--run-local-gateway"):
        pytest.skip("requires --run-local-gateway")
    if Path(sys.prefix).resolve() != (ROOT / ".venv").resolve():
        pytest.fail("Run local integration tests with the repository .venv interpreter")
    spec = Path(sys.prefix) / "share/jupyter/kernels/python3/kernel.json"
    if not spec.exists() or Path(json.loads(spec.read_text())["argv"][0]).resolve() != Path(sys.executable).resolve():
        pytest.fail("Install the repository venv's standard python3 ipykernel kernelspec first")
    provider = LocalRuntimeProvider(root=tmp_path)
    gateway = None
    started = time.monotonic()
    try:
        handle = await provider.provision(RuntimeSpec(request.node.nodeid[:128]))
        process = provider._runtimes[handle.runtime_id].process
        gateway = LocalGateway(handle.endpoint.url, handle.endpoint.token,
                               psutil.Process(process.pid), provider, handle.runtime_id,
                               time.monotonic() - started)
        yield gateway
    finally:
        if gateway:
            gateway.capture_children()
        await provider.close()
        if gateway:
            measurements = provider.measurements
            survivors = [child.pid for child in gateway.tracked if alive(child)]
            forced = any(m.get("forced_cleanup", False) for m in measurements)
            TIMINGS.append({"test": request.node.nodeid,
                            "gateway_startup_seconds": round(gateway.startup_seconds, 4),
                            "kernel_startup_seconds": gateway.kernel_startups,
                            "gateway_reaped": not alive(gateway.process),
                            "surviving_child_pids": survivors,
                            "forced_cleanup": forced,
                            "provider_measurements": measurements})
            assert not alive(gateway.process)
            assert not survivors, f"Orphaned Gateway descendants: {survivors}"
            assert not forced, "Normal Gateway teardown required force"


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
    if alive(local_gateway.process):
        async with aiohttp.ClientSession(
            headers={"Authorization": f"token {local_gateway.token}"},
            timeout=aiohttp.ClientTimeout(total=5),
        ) as http:
            async with http.get(f"{local_gateway.url}/api/kernels") as response:
                assert response.status == 200
                assert await response.json() == []
        assert not any(alive(child) for child in local_gateway.tracked)


@pytest.fixture(autouse=True)
def private_notebook_storage(tmp_path, monkeypatch):
    """Every app/child test uses disposable document storage, never the user's library."""
    monkeypatch.setenv('EUROPA_NOTEBOOK_ROOT', str(tmp_path / 'notebooks'))
    monkeypatch.setenv('EUROPA_APPLICATION_ROOT', str(tmp_path / 'application'))
