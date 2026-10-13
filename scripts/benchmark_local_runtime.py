"""Trusted, bounded local baseline; run using the repository .venv from its root."""
import asyncio
import json
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api/src"))

from kernel.gateway_client import GatewayClient
from runtime.contracts import RuntimeSpec
from runtime.local import LocalRuntimeProvider, alive


async def main():
    provider = LocalRuntimeProvider()
    captured = []
    report = {"provider": "local", "host": platform.platform(), "python": platform.python_version()}
    try:
        started = time.monotonic()
        handle = await provider.provision(RuntimeSpec("trusted-benchmark"))
        report["provision_through_gateway_ready_seconds"] = time.monotonic() - started
        async with GatewayClient(handle.endpoint.url, handle.endpoint.token) as client:
            started = time.monotonic()
            kernel = (await client.create_kernel())["id"]
            report["kernel_ready_seconds"] = time.monotonic() - started
            started = time.monotonic()
            result = await client.execute(kernel, "value = 42; print(value)")
            assert result["status"] == "ok"
            report["first_execution_seconds"] = time.monotonic() - started
            warm = []
            for _ in range(3):
                started = time.monotonic()
                result = await client.execute(kernel, "print(value)")
                assert result["status"] == "ok"
                warm.append(time.monotonic() - started)
            report["warm_execution_seconds"] = warm
            captured = list(provider.processes(handle.runtime_id))
            report["idle_gateway_and_kernel_rss_bytes"] = sum(p.memory_info().rss for p in captured if alive(p))
            report["idle_process_count"] = sum(alive(p) for p in captured)
    finally:
        started = time.monotonic()
        await provider.close()
        report["runtime_shutdown_cleanup_seconds"] = time.monotonic() - started
        report["provider_measurements"] = provider.measurements
        report["surviving_owned_pids"] = [p.pid for p in captured if alive(p)]
        assert not report["surviving_owned_pids"]
        target = Path("test-results")
        target.mkdir(exist_ok=True)
        (target / "step2b-local-benchmark.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
