from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Mapping, Protocol
import math
import re


class RuntimeState(StrEnum):
    STARTING = "starting"
    READY = "ready"
    STOPPED = "stopped"
    FAILED = "failed"
    DESTROYED = "destroyed"


@dataclass(frozen=True)
class ResourceRequests:
    cpu_cores: float | None = None
    memory_bytes: int | None = None
    pids_limit: int | None = None

    def __post_init__(self):
        if self.cpu_cores is not None and (isinstance(self.cpu_cores, bool) or
                not isinstance(self.cpu_cores, (int, float)) or not math.isfinite(self.cpu_cores) or self.cpu_cores <= 0):
            raise ValueError("CPU requests must be finite positive numbers")
        if any(value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0)
               for value in (self.memory_bytes, self.pids_limit)):
            raise ValueError("Memory and PID requests must be positive integers")


@dataclass(frozen=True)
class RuntimeSpec:
    notebook_id: str
    provider: str = "local"
    environment: Mapping[str, str] = field(default_factory=dict)
    working_directory: Path | None = None
    resources: ResourceRequests | None = None
    startup_timeout: float = 30
    resource_profile: str | None = None
    allocation_id: str | None = None

    def __post_init__(self):
        if not self.notebook_id.strip() or len(self.notebook_id) > 128:
            raise ValueError("A bounded notebook identifier is required")
        if not 0 < self.startup_timeout <= 300:
            raise ValueError("Startup timeout must be in (0, 300]")
        if self.resource_profile is not None and (not isinstance(self.resource_profile, str) or
                not self.resource_profile.strip() or len(self.resource_profile) > 64):
            raise ValueError("Resource profile must be a nonblank identifier of at most 64 characters")
        if self.allocation_id is not None and (not isinstance(self.allocation_id, str) or
                not re.fullmatch(r"europa-[a-f0-9]{32}", self.allocation_id)):
            raise ValueError("Invalid reserved allocation identity")


@dataclass(frozen=True)
class GatewayEndpoint:
    url: str
    token: str = field(repr=False)


@dataclass(frozen=True)
class RuntimeHandle:
    runtime_id: str
    provider: str
    notebook_id: str
    endpoint: GatewayEndpoint | None = field(repr=False)
    generation: int
    state: RuntimeState
    created_at: str
    started_at: str | None = None


@dataclass(frozen=True)
class RuntimeStatus:
    handle: RuntimeHandle
    process_alive: bool | None
    ready: bool
    failure: str | None = None
    resource_limits_enforced: bool = False


class RuntimeErrorBase(Exception):
    """Provider/supervisor failure safe to expose without credentials."""


class RuntimeProvider(Protocol):
    async def provision(self, spec: RuntimeSpec) -> RuntimeHandle:
        """Allocate an owned environment and start it, returning after readiness."""
        ...

    async def start(self, runtime_id: str) -> RuntimeHandle:
        """Start a stopped runtime; ready instances are returned idempotently."""
        ...

    async def inspect(self, runtime_id: str) -> RuntimeStatus: ...
    async def stop(self, runtime_id: str) -> None:
        """Stop/reap processes; retain environment and runtime identity."""
        ...

    async def destroy(self, runtime_id: str) -> None:
        """Stop and remove provider-owned environment; idempotent."""
        ...

    async def close(self) -> None:
        """Reclaim all owned allocations, including failed provisioning attempts."""
        ...
