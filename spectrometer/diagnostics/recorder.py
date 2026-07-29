"""Bounded, non-blocking JSONL diagnostic recorder."""

from __future__ import annotations

from collections import deque
import json
import os
from pathlib import Path
import queue
import threading
from typing import Callable

from .models import FORMAT_VERSION, json_line, json_safe, timestamp_fields
from .paths import new_run_id, run_directory, validate_id


class DiagnosticRecorder:
    def __init__(
        self,
        root: Path | None = None,
        *,
        queue_size: int = 512,
        warning: Callable[[str], None] | None = None,
    ):
        self.run_id = new_run_id()
        self.run_dir = run_directory(self.run_id, root)
        self.warning = warning
        self._queue: queue.Queue = queue.Queue(maxsize=max(8, queue_size))
        self._latest_samples: dict[str, tuple] = {}
        self._lock = threading.Lock()
        self._stop = object()
        self._thread: threading.Thread | None = None
        self._enabled = False
        self._warned = False
        self._dropped = 0
        self._coalesced = 0
        self._recent_frames = deque(maxlen=10)
        self._active_acquisition = ""
        self._latest_acquisition = ""
        self._manifest = {
            "format_version": FORMAT_VERSION,
            "run_id": self.run_id,
            "status": "active",
            **timestamp_fields(),
            "latest_acquisition_id": "",
        }
        try:
            self.run_dir.parent.mkdir(parents=True, exist_ok=True)
            self._mark_stale_sessions()
            self.run_dir.mkdir(parents=True, exist_ok=False)
            self._write_manifest()
            self._enabled = True
            self._thread = threading.Thread(
                target=self._writer_loop,
                name="diagnostic-recorder",
                daemon=True,
            )
            self._thread.start()
        except OSError as exc:
            self._disable(f"诊断记录目录不可写：{exc}")

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def latest_acquisition_id(self) -> str:
        return self._latest_acquisition

    @property
    def active_acquisition_id(self) -> str:
        return self._active_acquisition

    def start_acquisition(self, acquisition_id: str, request=None) -> None:
        acquisition_id = validate_id(acquisition_id)
        self._active_acquisition = acquisition_id
        self._latest_acquisition = acquisition_id
        self._manifest["latest_acquisition_id"] = acquisition_id
        self._write_manifest_safely()
        self.record_event(
            "acquisition_started",
            {"acquisition_id": acquisition_id, "request": json_safe(request)},
        )

    def finish_acquisition(self, acquisition_id: str, **result) -> None:
        self.record_event(
            "acquisition_finished",
            {"acquisition_id": acquisition_id, **result},
        )
        if self._active_acquisition == acquisition_id:
            self._active_acquisition = ""

    def record_event(self, event: str, payload=None, *, level: str = "INFO") -> None:
        self._put(
            "timeline",
            {
                "event": event,
                "level": level,
                "acquisition_id": self._active_acquisition,
                "payload": json_safe(payload or {}),
            },
            merge_key=None,
        )

    def record_sample(self, sample: dict, *, key: str = "system") -> None:
        self._put(
            "performance",
            {
                "sample_type": key,
                "acquisition_id": self._active_acquisition,
                **json_safe(sample),
            },
            merge_key=key,
        )

    def record_frame_summary(self, summary: dict) -> None:
        self._put(
            "frame-summaries",
            {
                "acquisition_id": self._active_acquisition,
                **json_safe(summary),
            },
            merge_key=f"frame-{summary.get('device_id', 0)}",
        )

    def set_recent_frames(self, frames) -> None:
        with self._lock:
            self._recent_frames.clear()
            self._recent_frames.extend(frames or [])

    def recent_frames(self) -> list:
        with self._lock:
            return list(self._recent_frames)

    def close(self, timeout: float = 2.0) -> None:
        if not self._enabled:
            return
        self._flush_latest()
        try:
            self._queue.put_nowait(self._stop)
        except queue.Full:
            self._drain_one_sample()
            try:
                self._queue.put_nowait(self._stop)
            except queue.Full:
                pass
        if self._thread is not None:
            self._thread.join(timeout=max(0.0, timeout))
        self._manifest["status"] = (
            "complete" if self._thread is None or not self._thread.is_alive()
            else "incomplete"
        )
        self._manifest["ended"] = timestamp_fields()
        self._write_manifest_safely()
        self._enabled = False

    def _put(self, stream: str, payload: dict, merge_key: str | None) -> None:
        if not self._enabled:
            return
        item = (stream, payload)
        try:
            self._queue.put_nowait(item)
            return
        except queue.Full:
            pass
        if merge_key is not None:
            with self._lock:
                self._latest_samples[merge_key] = item
                self._coalesced += 1
        else:
            with self._lock:
                self._dropped += 1

    def _flush_latest(self) -> None:
        with self._lock:
            items = list(self._latest_samples.values())
            self._latest_samples.clear()
        for item in items:
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                break

    def _drain_one_sample(self) -> None:
        try:
            self._queue.get_nowait()
        except queue.Empty:
            pass

    def _writer_loop(self) -> None:
        handles = {}
        try:
            for name in ("timeline", "performance", "frame-summaries"):
                handles[name] = (self.run_dir / f"{name}.jsonl").open(
                    "a", encoding="utf-8", buffering=1
                )
            while True:
                item = self._queue.get()
                if item is self._stop:
                    break
                stream, payload = item
                with self._lock:
                    dropped, coalesced = self._dropped, self._coalesced
                    self._dropped = self._coalesced = 0
                if dropped or coalesced:
                    payload = dict(payload)
                    payload["diagnostic_pressure"] = {
                        "dropped_events": dropped,
                        "coalesced_samples": coalesced,
                    }
                handles[stream].write(json_line(stream, payload))
                self._flush_latest()
        except OSError as exc:
            self._disable(f"诊断记录写入失败：{exc}")
        finally:
            for handle in handles.values():
                try:
                    handle.flush()
                    os.fsync(handle.fileno())
                    handle.close()
                except OSError:
                    pass

    def _write_manifest(self) -> None:
        target = self.run_dir / "manifest.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(json_safe(self._manifest), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, target)

    def _mark_stale_sessions(self) -> None:
        for path in self.run_dir.parent.glob("run-*"):
            manifest_path = path / "manifest.json"
            try:
                values = json.loads(manifest_path.read_text(encoding="utf-8"))
                if (
                    values.get("run_id") != path.name
                    or values.get("status") != "active"
                ):
                    continue
                values["status"] = "incomplete"
                values["recovered_on_next_start"] = timestamp_fields()
                temporary = manifest_path.with_suffix(".json.tmp")
                temporary.write_text(
                    json.dumps(values, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                os.replace(temporary, manifest_path)
            except (OSError, ValueError):
                continue

    def _write_manifest_safely(self) -> None:
        if not self._enabled:
            return
        try:
            self._write_manifest()
        except OSError as exc:
            self._disable(f"诊断清单写入失败：{exc}")

    def _disable(self, message: str) -> None:
        self._enabled = False
        if not self._warned and self.warning is not None:
            self._warned = True
            self.warning(message)
