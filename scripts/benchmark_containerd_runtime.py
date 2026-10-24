"""Sequential trusted-code benchmark using an operator-configured containerd runtime.

Run from the repository root in its Linux virtual environment. Each sample owns
a fresh container and kernel; image-cache state is recorded, never called a cold pull.
"""
import asyncio
import json
import os
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api/src"))

from kernel.gateway_client import GatewayClient
from runtime.containerd import ContainerdConfig, ContainerdRuntimeProvider
from runtime.containerd_backend import SDKBridgeBackend
from runtime.contracts import ResourceRequests, RuntimeSpec


async def main():
    if os.environ.get("EUROPA_CONTAINERD_ALLOW_NETWORK") != "1":
        raise RuntimeError("Explicit operator-approved containerd networking is required")
    resources = ResourceRequests(float(os.environ["EUROPA_CONTAINERD_CPU_CORES"]),
                                int(os.environ["EUROPA_CONTAINERD_MEMORY_BYTES"]),
                                int(os.environ["EUROPA_CONTAINERD_PIDS_LIMIT"]))
    handler = os.environ.get("EUROPA_CONTAINERD_HANDLER", "runc")
    backend = SDKBridgeBackend(Path(os.environ["EUROPA_CONTAINERD_BRIDGE"]),
                               socket=os.environ.get("EUROPA_CONTAINERD_SOCKET", "/run/containerd/containerd.sock"),
                               cni_config=os.environ["EUROPA_CONTAINERD_CNI_CONFIG"],
                               cni_bin=os.environ.get("EUROPA_CONTAINERD_CNI_BIN", "/opt/cni/bin"))
    provider = ContainerdRuntimeProvider(backend, ContainerdConfig(os.environ["EUROPA_CONTAINERD_IMAGE"], handler))
    report = {"handler": handler, "image": provider.config.image,
              "host": platform.platform(), "samples": []}
    try:
        for number in range(3):
            sample = {}
            client = None
            handle = None
            try:
                started = time.monotonic()
                handle = await provider.provision(RuntimeSpec(f"benchmark-{number}", provider="containerd", resources=resources))
                sample["fresh_container_gateway_ready_seconds"] = time.monotonic() - started
                client = GatewayClient(handle.endpoint.url, handle.endpoint.token)
                await client.__aenter__()
                started = time.monotonic()
                kernel = (await client.create_kernel())["id"]
                sample["kernel_ready_seconds"] = time.monotonic() - started
                started = time.monotonic()
                result = await client.execute(kernel, "value = 42; print(value)")
                assert result["status"] == "ok"
                sample["first_execution_seconds"] = time.monotonic() - started
                warm = []
                for _ in range(3):
                    started = time.monotonic()
                    result = await client.execute(kernel, "value")
                    assert result["status"] == "ok"
                    warm.append(time.monotonic() - started)
                sample["warm_execution_seconds"] = warm
                observation = await backend.request("inspect", handle.runtime_id)
                assert observation.get("limits_verified")
                sample["idle_cgroup_memory_bytes"] = observation["memory_bytes"]
                sample["cgroup_limits"] = {key: observation[key] for key in ("memory_limit", "pids_limit")}
                sample["cpu_cores_requested"] = resources.cpu_cores
                sample["limits_verified"] = observation["limits_verified"]
            finally:
                started = time.monotonic()
                if client:
                    await client.close()
                sample["kernel_cleanup_seconds"] = time.monotonic() - started
                started = time.monotonic()
                if handle:
                    await provider.destroy(handle.runtime_id)
                    inventory = await backend.request("inventory", handle.runtime_id)
                    assert inventory == {"containers": 0, "tasks": 0, "snapshot_present": False,
                                         "network_state_present": False}, inventory
                sample["runtime_cleanup_and_inventory_seconds"] = time.monotonic() - started
                report["samples"].append(sample)
    finally:
        await provider.close()
        report["provider_measurements"] = provider.measurements
        target = Path("test-results")
        target.mkdir(exist_ok=True)
        (target / "step2b-containerd-benchmark.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
