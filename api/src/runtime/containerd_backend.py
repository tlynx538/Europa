"""Structured bridge to the supported containerd Go SDK, never ctr parsing."""
import asyncio
import json
import os
import platform
from pathlib import Path
from typing import Protocol

from runtime.contracts import RuntimeErrorBase


class ContainerdBackend(Protocol):
    async def request(self, operation: str, runtime_id: str, **parameters) -> dict: ...


class SDKBridgeBackend:
    def __init__(self, executable: Path, *, socket: str = "/run/containerd/containerd.sock",
                 namespace: str = "europa", cni_config: str = "/etc/cni/net.d/europa.conflist",
                 cni_bin: str = "/opt/cni/bin", state_root: str = "/run/europa", timeout: float = 20):
        self.executable = executable.resolve()
        self.socket = socket
        self.namespace = namespace
        self.cni_config = cni_config
        self.cni_bin = cni_bin
        self.state_root = state_root
        self.timeout = timeout

    async def request(self, operation: str, runtime_id: str, **parameters):
        if platform.system() != "Linux":
            raise RuntimeErrorBase("Containerd SDK bridge requires a Linux host")
        if not self.executable.is_file():
            raise RuntimeErrorBase("Containerd SDK bridge executable is missing")
        request = {"operation": operation, "id": runtime_id, "socket": self.socket,
                   "namespace": self.namespace, "cni_config": self.cni_config,
                   "cni_bin": self.cni_bin, "state_root": self.state_root,
                   "timeout_seconds": self.timeout, **parameters}
        process = None
        try:
            spawning = asyncio.create_task(asyncio.create_subprocess_exec(
                str(self.executable), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
            ))
            try:
                process = await asyncio.shield(spawning)
            except asyncio.CancelledError:
                process = await spawning
                raise
            async with asyncio.timeout(self.timeout + 2):
                output, _ = await process.communicate(json.dumps(request).encode())
            result = json.loads(output)
            if process.returncode or not isinstance(result, dict) or result.get("error"):
                raise RuntimeErrorBase("Containerd backend operation failed")
            return result
        except (OSError, ValueError, TimeoutError) as exc:
            raise RuntimeErrorBase("Containerd backend transport failed") from exc
        finally:
            if process and process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
