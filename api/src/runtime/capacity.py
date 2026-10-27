"""Read-only Linux host/cgroup-v2 capacity detection and single-writer guard."""
import os
from pathlib import Path

from runtime.admission import HostCapacity, AdmissionRejected


def cpuset_size(text):
    cpus = set()
    for part in text.strip().split(","):
        if not part:
            raise ValueError("Empty effective CPU set")
        bounds = [int(x) for x in part.split("-")]
        if len(bounds) not in (1, 2) or min(bounds) < 0 or bounds[-1] < bounds[0] or bounds[-1] > 1048576:
            raise ValueError("Invalid effective CPU set")
        cpus.update(range(bounds[0], bounds[-1] + 1))
    return len(cpus)


class LinuxCapacityDetector:
    def __init__(self, proc=Path("/proc"), affinity=None):
        self.proc = Path(proc)
        self.affinity = affinity or (lambda: os.sched_getaffinity(0))

    def detect(self):
        memory = {}
        for line in (self.proc / "meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            if key in ("MemTotal", "MemAvailable"):
                number, unit = value.split()
                if unit != "kB":
                    raise ValueError("Unknown memory unit")
                memory[key] = int(number) * 1024
        total, available = memory["MemTotal"], memory["MemAvailable"]
        if total <= 0 or not 0 <= available <= total:
            raise ValueError("Invalid host memory observation")
        cpu = float(len(self.affinity()))
        membership = [line.split(":", 2)[2] for line in (self.proc / "self/cgroup").read_text().splitlines()
                      if line.startswith("0::")]
        mounts = []
        for line in (self.proc / "self/mountinfo").read_text().splitlines():
            left, right = line.split(" - ", 1)
            if right.split()[0] == "cgroup2":
                fields = left.split()
                # Linux mountinfo escapes path spaces/backslashes as octal sequences.
                def unescape(text):
                    import re
                    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), text)
                mounts.append((Path(unescape(fields[3])), Path(unescape(fields[4]))))
        if len(membership) != 1 or len(mounts) != 1:
            raise ValueError("Reliable cgroup v2 hierarchy required")
        mount_root, mount_point = mounts[0]
        relative = Path(membership[0]).relative_to(mount_root)
        if ".." in relative.parts:
            raise ValueError("Invalid cgroup membership")
        current = mount_point / relative
        # Detect all ancestor constraints visible from this mount. A delegated
        # mount hides ancestors, so deny rather than claim a complete capacity.
        if mount_root != Path("/"):
            raise ValueError("Cgroup ancestors are hidden by a delegated mount")
        while True:
            for limit_file, usage_file in (("memory.max", "memory.current"),):
                path = current / limit_file
                if path.exists():
                    limit = path.read_text().strip()
                    if limit != "max":
                        limit = int(limit)
                        usage = int((current / usage_file).read_text().strip())
                        if limit <= 0 or usage < 0:
                            raise ValueError("Invalid cgroup memory controls")
                        total = min(total, limit)
                        available = min(available, max(0, limit - usage))
                elif current != mount_point:
                    raise ValueError("Missing cgroup memory controller")
            path = current / "cpu.max"
            if path.exists():
                quota, period = path.read_text().split()
                period = int(period)
                if period <= 0:
                    raise ValueError("Invalid CPU period")
                if quota != "max":
                    if int(quota) <= 0:
                        raise ValueError("Invalid CPU quota")
                    cpu = min(cpu, int(quota) / period)
            elif current != mount_point:
                raise ValueError("Missing cgroup CPU controller")
            path = current / "cpuset.cpus.effective"
            if path.exists():
                cpu = min(cpu, cpuset_size(path.read_text()))
            if current == mount_point:
                break
            current = current.parent
        return HostCapacity(total, min(total, available), cpu)


class SingleWriterGuard:
    """One admitted control plane per namespace on this node; no service installed."""
    def __init__(self, path):
        self.path = Path(path)
        self.fd = None

    def acquire(self):
        import fcntl
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(fd)
            raise AdmissionRejected("Another notebook admission controller owns this namespace")
        self.fd = fd

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
