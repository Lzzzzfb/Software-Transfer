"""最近一次正常采集的待手动保存状态。"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple

from .export_context import SealedExportContext


@dataclass(frozen=True)
class PendingManualCapture:
    context: SealedExportContext
    created_at: str

    @classmethod
    def create(cls, context: SealedExportContext):
        return cls(
            context=context,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    @property
    def task_id(self) -> str:
        return self.context.request.task_id

    @property
    def spool_paths(self) -> Tuple[Path, ...]:
        return tuple(self.context.spool_paths.values())

    @property
    def frame_count(self) -> int:
        return sum(self.context.expected_counts.values())
