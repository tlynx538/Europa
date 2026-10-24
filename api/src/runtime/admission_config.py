"""Validated operator configuration; no system configuration writes."""
import hashlib
import json
import os
import tempfile
from pathlib import Path

from runtime.admission import AdmissionController, AdmissionPolicy, ResourceProfiles
from runtime.capacity import LinuxCapacityDetector, SingleWriterGuard
from runtime.contracts import ResourceRequests


def configured_admission(socket):
    raw = os.environ.get("EUROPA_RESOURCE_PROFILES")
    legacy = [os.environ.get("EUROPA_CONTAINERD_" + suffix) for suffix in
              ("CPU_CORES", "MEMORY_BYTES", "PIDS_LIMIT")]
    profiles = None
    if raw is not None:
        mapping = json.loads(raw)
        if not isinstance(mapping, dict):
            raise ValueError("EUROPA_RESOURCE_PROFILES must be an object of complete resource profiles")
        profiles = {name: ResourceRequests(**values) for name, values in mapping.items()}
    elif any(value is not None for value in legacy):
        if not all(value is not None for value in legacy):
            raise ValueError("Legacy containerd resource settings must specify CPU, memory and PIDs together")
        profiles = {"standard": ResourceRequests(float(legacy[0]), int(legacy[1]), int(legacy[2]))}
    def optional(name, convert):
        value = os.environ.get("EUROPA_ADMISSION_" + name)
        return None if value is None else convert(value)
    policy = AdmissionPolicy(memory_reserve=optional("MEMORY_RESERVE_BYTES", int),
                             cpu_reserve=optional("CPU_RESERVE_CORES", float),
                             memory_ceiling=optional("MEMORY_CAPACITY_BYTES", int),
                             cpu_ceiling=optional("CPU_CAPACITY_CORES", float))
    catalog = ResourceProfiles(profiles, os.environ.get("EUROPA_RESOURCE_DEFAULT_PROFILE", "standard"))
    identity = hashlib.sha256((str(Path(socket).resolve()) + ":europa").encode()).hexdigest()[:16]
    lock_path = os.environ.get("EUROPA_ADMISSION_LOCK", str(Path(tempfile.gettempdir()) / f"europa-admission-{identity}.lock"))
    return AdmissionController(LinuxCapacityDetector(), catalog, policy), SingleWriterGuard(lock_path)
