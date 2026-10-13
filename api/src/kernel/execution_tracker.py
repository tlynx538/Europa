"""Europa metadata only. Jupyter owns kernel and execution machinery."""
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone


@dataclass
class ExecutionRecord:
    execution_id: str
    kernel_id: str
    status: str
    finished_at: str


class ExecutionTracker:
    def __init__(self, limit: int = 50):
        self.records: deque[ExecutionRecord] = deque(maxlen=limit)

    def finish(self, execution_id: str, kernel_id: str, status: str) -> None:
        self.records.append(ExecutionRecord(
            execution_id, kernel_id, status, datetime.now(timezone.utc).isoformat()
        ))

    def snapshot(self) -> list[dict]:
        return [asdict(record) for record in self.records]
