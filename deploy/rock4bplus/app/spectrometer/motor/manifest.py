"""Atomic, low-frequency scan run manifest."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from .scan import ScanPlan


MANIFEST_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ScanManifestWriter:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path: Path | None = None
        self._payload: dict | None = None

    def start(
        self,
        plan: ScanPlan,
        *,
        motor_device_id: str,
        spectrometer_device_ids: tuple[int, ...],
    ) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        scan_id = uuid.uuid4().hex
        timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        self.path = self.directory / f"scan-{timestamp}-{scan_id[:8]}.json"
        self._payload = {
            "version": MANIFEST_VERSION,
            "scan_id": scan_id,
            "status": "running",
            "reason": "",
            "started_at": _now(),
            "finished_at": "",
            "parameters": asdict(plan.parameters),
            "start_position": asdict(plan.start),
            "motor_device_id": str(motor_device_id),
            "spectrometer_device_ids": list(spectrometer_device_ids),
            "rounds": [
                {
                    "index": round_plan.index,
                    "task_id": "",
                    "acquisition_started": False,
                    "acquisition_completed": False,
                    "return_completed": False,
                    "completed": False,
                    "failed": False,
                    "reason": "",
                    "files": [],
                }
                for round_plan in plan.rounds
            ],
        }
        self._write()
        return self.path

    def _round(self, index: int) -> dict:
        if self._payload is None:
            raise RuntimeError("scan manifest has not been started")
        return self._payload["rounds"][index - 1]

    def acquisition_started(self, index: int, task_id: str):
        record = self._round(index)
        record["task_id"] = str(task_id)
        record["acquisition_started"] = True
        self._write()

    def acquisition_finished(
        self,
        index: int,
        files,
        *,
        failed: bool,
        reason: str = "",
    ):
        record = self._round(index)
        record["acquisition_completed"] = not failed
        record["failed"] = bool(failed)
        record["reason"] = str(reason)
        record["files"] = [str(path) for path in files]
        self._write()

    def return_finished(
        self,
        index: int,
        *,
        completed: bool,
        reason: str = "",
    ):
        record = self._round(index)
        record["return_completed"] = bool(completed)
        record["completed"] = (
            bool(completed)
            and record["acquisition_completed"]
            and not record["failed"]
        )
        if reason:
            record["reason"] = str(reason)
            record["failed"] = True
        self._write()

    def finish(self, status: str, reason: str = ""):
        if self._payload is None:
            return
        self._payload["status"] = str(status)
        self._payload["reason"] = str(reason)
        self._payload["finished_at"] = _now()
        self._write()

    def _write(self):
        if self.path is None or self._payload is None:
            return
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                self._payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.path)
