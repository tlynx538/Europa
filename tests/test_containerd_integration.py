"""Opt-in disposable container tests. Missing infrastructure is reported as SKIP."""
import asyncio
import json
import os
import platform
import shutil
import time
from pathlib import Path

import pytest
import pytest_asyncio

from kernel.gateway_client import GatewayClient
from runtime.containerd import ContainerdConfig, ContainerdRuntimeProvider
from runtime.containerd_backend import SDKBridgeBackend
from runtime.contracts import ResourceRequests, RuntimeSpec

pytestmark = pytest.mark.containerd_integration
BENCHMARKS = []


def stdout(result):
    return "".join(m["content"]["text"] for m in result["messages"] if m["header"]["msg_type"] == "stream")


@pytest_asyncio.fixture
async def sandbox(request):
    handler = request.param
    if platform.system() != "Linux":
        pytest.skip("Linux required; macOS host has no containerd/runc/runsc/socket or identified Linux VM")
    if not request.config.getoption("--run-containerd"):
        pytest.skip("requires --run-containerd and approved containerd/CNI infrastructure")
    required = ["EUROPA_CONTAINERD_BRIDGE", "EUROPA_CONTAINERD_IMAGE", "EUROPA_CONTAINERD_CNI_CONFIG",
                "EUROPA_CONTAINERD_CPU_CORES", "EUROPA_CONTAINERD_MEMORY_BYTES", "EUROPA_CONTAINERD_PIDS_LIMIT"]
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        pytest.skip("missing operator configuration: " + ", ".join(missing))
    if os.environ.get("EUROPA_CONTAINERD_ALLOW_NETWORK") != "1":
        pytest.skip("explicit CNI networking approval required")
    for binary in [handler, "containerd-shim-runsc-v1" if handler == "runsc" else "containerd-shim-runc-v2"]:
        if not shutil.which(binary): pytest.skip(f"missing {binary}")
    backend = SDKBridgeBackend(Path(os.environ["EUROPA_CONTAINERD_BRIDGE"]),
        socket=os.environ.get("EUROPA_CONTAINERD_SOCKET", "/run/containerd/containerd.sock"),
        cni_config=os.environ["EUROPA_CONTAINERD_CNI_CONFIG"],
        cni_bin=os.environ.get("EUROPA_CONTAINERD_CNI_BIN", "/opt/cni/bin"))
    provider = ContainerdRuntimeProvider(backend, ContainerdConfig(os.environ["EUROPA_CONTAINERD_IMAGE"], handler))
    resources = ResourceRequests(float(os.environ["EUROPA_CONTAINERD_CPU_CORES"]),
        int(os.environ["EUROPA_CONTAINERD_MEMORY_BYTES"]), int(os.environ["EUROPA_CONTAINERD_PIDS_LIMIT"]))
    try:
        yield provider, resources
    finally:
        await provider.close()
        for runtime_id in provider._runtimes:
            inventory = await backend.request("inventory", runtime_id)
            assert inventory == {"containers": 0, "tasks": 0, "snapshot_present": False,
                                 "network_state_present": False}
        BENCHMARKS.append({"handler": handler, "measurements": provider.measurements})
        target = Path("test-results")
        target.mkdir(exist_ok=True)
        (target / "containerd-benchmarks.json").write_text(json.dumps(BENCHMARKS, indent=2) + "\n")


CASES = ["creation", "gateway", "kernel", "persistence", "rich_error", "restart", "interrupt",
         "shutdown", "forced", "independent", "crash", "limits", "no_leaks", "benchmark"]


@pytest.mark.parametrize("sandbox", ["runc", "runsc"], indirect=True)
@pytest.mark.parametrize("case", CASES)
async def test_real_container_runtime(sandbox, case):
    provider, resources = sandbox
    handle = await provider.provision(RuntimeSpec("A", provider="containerd", resources=resources))
    if case == "creation":
        assert (await provider.inspect(handle.runtime_id)).process_alive
        return
    async with GatewayClient(handle.endpoint.url, handle.endpoint.token) as client:
        if case == "gateway":
            assert "python3" in (await client.health())["kernelspecs"]
            assert await client._request("GET", "/api/kernels") == []
            return
        started = time.monotonic()
        kernel = (await client.create_kernel())["id"]
        provider.measurements.append({"kernel_ready_seconds": time.monotonic() - started})
        started = time.monotonic()
        assert stdout(await client.execute(kernel, "value = 42; print(value)")) == "42\n"
        provider.measurements.append({"first_execution_seconds": time.monotonic() - started})
        if case == "persistence": assert stdout(await client.execute(kernel, "print(value)")) == "42\n"
        if case == "rich_error":
            result = await client.execute(kernel, "from IPython.display import display, HTML; display(HTML('<b>rich</b>'))")
            assert any("text/html" in m["content"].get("data", {}) for m in result["messages"])
            assert (await client.execute(kernel, "raise ValueError('trusted')"))["reply"]["ename"] == "ValueError"
        if case == "restart":
            await client.restart_kernel(kernel)
            assert stdout(await client.execute(kernel, "print('value' in globals())")) == "False\n"
        if case == "interrupt":
            session_id = "observer"
            async with client._session_ready().ws_connect(client._websocket_url(kernel, session_id)) as observer:
                await asyncio.wait_for(client._handshake(observer, session_id), 5)
                task = asyncio.create_task(client.execute(kernel, "import time; time.sleep(5)"))
                try:
                    async with asyncio.timeout(5):
                        while (await client._receive(observer))["header"].get("msg_type") != "execute_input": pass
                    await client.interrupt_kernel(kernel)
                    assert (await asyncio.wait_for(task, 5))["reply"]["ename"] == "KeyboardInterrupt"
                finally:
                    if not task.done(): task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        if case == "independent":
            other = await provider.provision(RuntimeSpec("B", provider="containerd", resources=resources))
            assert other.runtime_id != handle.runtime_id and other.endpoint.url != handle.endpoint.url
            async with GatewayClient(other.endpoint.url, other.endpoint.token) as second:
                k2 = (await second.create_kernel())["id"]
                assert stdout(await second.execute(k2, "print('value' in globals())")) == "False\n"
                await provider.destroy(handle.runtime_id)
                assert stdout(await second.execute(k2, "print(42)")) == "42\n"
        if case == "limits":
            # Verify actual host cgroup v2 controls, not just OCI request fields.
            assert (await provider.inspect(handle.runtime_id)).resource_limits_enforced
        if case == "benchmark":
            for _ in range(3):
                started = time.monotonic()
                await client.execute(kernel, "print(value)")
                provider.measurements.append({"warm_execution_seconds": time.monotonic() - started})
            observed = await provider.backend.request("inspect", handle.runtime_id)
            assert observed.get("memory_bytes") is not None
            provider.measurements.append({"idle_memory_bytes": observed["memory_bytes"]})
        if case == "forced":
            await provider.backend.request("pause", handle.runtime_id)
            await provider.stop(handle.runtime_id)
            assert provider.measurements[-1]["forced_cleanup"]
        if case == "crash":
            await client.execute(kernel, "print('trusted bounded crash test')")
            await provider.backend.request("kill", handle.runtime_id)
            async with asyncio.timeout(5):
                while (await provider.inspect(handle.runtime_id)).ready:
                    await asyncio.sleep(0.05)
        if case in {"shutdown", "no_leaks"}:
            await client.terminate_kernel(kernel)
    await provider.destroy(handle.runtime_id)
    await provider.destroy(handle.runtime_id)
