"""Single-process admission: full limit reservations, no queues or usage prediction."""
import asyncio
import math
from dataclasses import asdict, dataclass
from types import MappingProxyType

from runtime.contracts import ResourceRequests, RuntimeErrorBase, RuntimeSpec


class AdmissionRejected(RuntimeErrorBase):
    """A safe, caller-readable admission failure (HTTP 503)."""


def validate_resources(resources):
    if not isinstance(resources, ResourceRequests):
        raise ValueError("A complete CPU, memory and PID profile is required")
    cpu, memory, pids = resources.cpu_cores, resources.memory_bytes, resources.pids_limit
    if (isinstance(cpu, bool) or not isinstance(cpu, (int, float)) or
            not math.isfinite(cpu) or not .001 <= cpu <= 1024):
        raise ValueError("CPU cores must be finite and in [0.001, 1024]")
    if any(isinstance(x, bool) or not isinstance(x, int) or x <= 0 for x in (memory, pids)):
        raise ValueError("Memory bytes and PID limit must be positive integers")
    if memory > 2**63 - 1 or pids > 2**31 - 1:
        raise ValueError("Memory or PID limit exceeds the supported range")


class ResourceProfiles:
    def __init__(self, profiles=None, default="standard"):
        profiles = profiles if profiles is not None else {"standard": ResourceRequests(.5, 512 * 1024**2, 128)}
        self.profiles = MappingProxyType(dict(profiles))
        self.default = default
        if not self.profiles or default not in self.profiles:
            raise ValueError("Default resource profile must exist")
        for name, resources in self.profiles.items():
            if not isinstance(name, str) or not name.strip() or len(name) > 64:
                raise ValueError("Profile IDs must be nonblank strings of at most 64 characters")
            validate_resources(resources)

    def resolve(self, spec: RuntimeSpec):
        if spec.resources is not None and spec.resource_profile is not None:
            raise AdmissionRejected("Choose a resource profile or explicit resources, not both")
        if spec.resources is not None:
            try:
                validate_resources(spec.resources)
            except ValueError as exc:
                raise AdmissionRejected(str(exc)) from exc
            return None, spec.resources
        name = spec.resource_profile if spec.resource_profile is not None else self.default
        try:
            return name, self.profiles[name]
        except KeyError as exc:
            raise AdmissionRejected("Unknown notebook resource profile") from exc


@dataclass(frozen=True)
class HostCapacity:
    memory_total: int
    memory_available: int
    cpu_cores: float

    def __post_init__(self):
        if (isinstance(self.memory_total, bool) or isinstance(self.memory_available, bool)
                or not isinstance(self.memory_total, int) or not isinstance(self.memory_available, int)
                or self.memory_total <= 0 or not 0 <= self.memory_available <= self.memory_total
                or isinstance(self.cpu_cores, bool) or not isinstance(self.cpu_cores, (int, float))
                or not math.isfinite(self.cpu_cores) or self.cpu_cores <= 0):
            raise ValueError("Host capacity is invalid")


@dataclass(frozen=True)
class AdmissionPolicy:
    memory_reserve: int | None = None
    cpu_reserve: float | None = None
    memory_ceiling: int | None = None
    cpu_ceiling: float | None = None

    def __post_init__(self):
        for value in (self.memory_reserve, self.memory_ceiling):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError("Memory reserve/ceiling must be nonnegative integer bytes")
        for value in (self.cpu_reserve, self.cpu_ceiling):
            if value is not None and (isinstance(value, bool) or not math.isfinite(value) or value < 0):
                raise ValueError("CPU reserve/ceiling must be finite and nonnegative")

    def budget(self, observed):
        # Host resources are detected, never assumed to be the development VM's size.
        memory_reserve = self.memory_reserve if self.memory_reserve is not None else max(256 * 1024**2, observed.memory_total // 10)
        cpu_reserve = self.cpu_reserve if self.cpu_reserve is not None else observed.cpu_cores / 10
        memory = max(0, observed.memory_total - memory_reserve)
        cpu = max(0, observed.cpu_cores - cpu_reserve)
        if self.memory_ceiling is not None:
            memory = min(memory, self.memory_ceiling)
        if self.cpu_ceiling is not None:
            cpu = min(cpu, self.cpu_ceiling)
        return memory, cpu, memory_reserve, cpu_reserve


class AdmissionController:
    def __init__(self, detector, profiles=None, policy=None):
        self.detector = detector
        self.profiles = profiles or ResourceProfiles()
        self.policy = policy or AdmissionPolicy()
        self._lock = asyncio.Lock()
        self._reservations = {}
        self.rejections = 0
        self.cleanup_failures = 0

    async def _capacity(self):
        try:
            return await asyncio.to_thread(self.detector.detect)
        except Exception as exc:
            raise AdmissionRejected("Host capacity could not be determined reliably; admission denied") from exc

    def _metrics(self, observed):
        total_memory, total_cpu, reserve_memory, reserve_cpu = self.policy.budget(observed)
        resources = [r[1] for r in self._reservations.values()]
        used_memory = sum(r.memory_bytes for r in resources)
        used_cpu = sum(r.cpu_cores for r in resources)
        # Deliberately conservative: subtract full reservations from observed free
        # memory too, even if some of their usage is already in that observation.
        available_memory = max(0, min(total_memory - used_memory,
                                     observed.memory_available - reserve_memory - used_memory))
        return {"observed": asdict(observed),
                "host_reserve": {"memory_bytes": reserve_memory, "cpu_cores": reserve_cpu},
                "allocatable": {"memory_bytes": total_memory, "cpu_cores": total_cpu},
                "reserved": {"memory_bytes": used_memory, "cpu_cores": used_cpu,
                             "pids_limit_sum": sum(r.pids_limit for r in resources)},
                "available_admission": {"memory_bytes": available_memory,
                                        "cpu_cores": max(0, total_cpu - used_cpu)},
                "reservation_count": len(resources),
                "active_notebook_runtime_count": sum(r[2] == "active" for r in self._reservations.values()),
                "admission_rejections": self.rejections,
                "reservation_cleanup_failures": self.cleanup_failures}

    async def reserve(self, key, spec):
        async with self._lock:
            try:
                profile, resources = self.profiles.resolve(spec)
                if key in self._reservations:
                    raise AdmissionRejected("Notebook already has a reservation; cleanup is required")
                observed = await self._capacity()
                available = self._metrics(observed)["available_admission"]
                if resources.memory_bytes > available["memory_bytes"]:
                    raise AdmissionRejected("Insufficient memory after host reserve and notebook reservations")
                if resources.cpu_cores > available["cpu_cores"] + 1e-9:
                    raise AdmissionRejected("Insufficient CPU capacity after host reserve and notebook reservations")
                self._reservations[key] = (profile, resources, "starting")
                return profile, resources
            except AdmissionRejected:
                self.rejections += 1
                raise

    async def activate(self, key):
        async with self._lock:
            profile, resources, _ = self._reservations[key]
            self._reservations[key] = (profile, resources, "active")

    async def restore(self, key, profile, resources):
        """Restore occupied capacity even when it exceeds today's admission budget."""
        validate_resources(resources)
        async with self._lock:
            if key in self._reservations:
                raise AdmissionRejected("Duplicate durable notebook reservation")
            self._reservations[key] = (profile, resources, "cleanup_pending")

    async def retain(self, key, *, cleanup_failed=False):
        async with self._lock:
            if key in self._reservations:
                profile, resources, _ = self._reservations[key]
                self._reservations[key] = (profile, resources, "cleanup_pending")
                if cleanup_failed:
                    self.cleanup_failures += 1

    async def release(self, key):
        async with self._lock:
            self._reservations.pop(key, None)

    async def reservation(self, key):
        async with self._lock:
            value = self._reservations.get(key)
            if value is None:
                return None
            profile, resources, phase = value
            return {"profile_id": profile, "resources": asdict(resources), "phase": phase}

    async def metrics(self):
        async with self._lock:
            try:
                return {"enabled": True, **self._metrics(await self._capacity())}
            except AdmissionRejected as exc:
                return {"enabled": True, "capacity_error": str(exc),
                        "allocatable": None,
                        "reserved": {"memory_bytes": sum(r[1].memory_bytes for r in self._reservations.values()),
                                     "cpu_cores": sum(r[1].cpu_cores for r in self._reservations.values()),
                                     "pids_limit_sum": sum(r[1].pids_limit for r in self._reservations.values())},
                        "active_notebook_runtime_count": sum(r[2] == "active" for r in self._reservations.values()),
                        "available_admission": None, "reservation_count": len(self._reservations),
                        "admission_rejections": self.rejections,
                        "reservation_cleanup_failures": self.cleanup_failures}
