import json
import os
from pathlib import Path
import time

import pytest

from spectrometer.diagnostics.models import json_line
from spectrometer.diagnostics.paths import run_directory, validate_id
from spectrometer.diagnostics.recorder import DiagnosticRecorder
from spectrometer.diagnostics.retention import enforce_retention
from spectrometer.diagnostics.system_sampler import process_sample, sample_system


def test_diagnostic_ids_reject_path_escape(tmp_path):
    assert run_directory("run-safe_123", tmp_path).parent == tmp_path
    for value in ("../escape", "/absolute", "has space"):
        with pytest.raises(ValueError):
            validate_id(value)


def test_json_line_has_stable_timestamps():
    values = json.loads(json_line("event", {"message": "正常"}))
    assert values["format_version"] == 1
    assert values["kind"] == "event"
    assert values["local_time"]
    assert values["monotonic_ns"] > 0


def test_recorder_persists_session_without_full_frames(tmp_path):
    recorder = DiagnosticRecorder(tmp_path)
    recorder.start_acquisition("task-123", {"mode": "continuous"})
    recorder.record_event("connected", {"port": "ttyACM0"})
    recorder.record_sample({"raw_complete_frames": 90}, key="device-0")
    recorder.record_frame_summary({"device_id": 0, "sha256": "abc"})
    recorder.finish_acquisition("task-123", failed=False)
    recorder.close()

    manifest = json.loads(
        (recorder.run_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "complete"
    assert manifest["latest_acquisition_id"] == "task-123"
    assert "connected" in (recorder.run_dir / "timeline.jsonl").read_text(
        encoding="utf-8"
    )
    assert not (recorder.run_dir / "recent-frames").exists()


def test_recorder_producer_never_waits_on_full_queue(tmp_path):
    recorder = DiagnosticRecorder(tmp_path, queue_size=8)
    started = time.perf_counter()
    for index in range(5000):
        recorder.record_sample({"value": index}, key="same")
    elapsed = time.perf_counter() - started
    recorder.close()
    assert elapsed < 1.0


def _make_session(root: Path, name: str, age_days: int, size: int):
    path = root / name
    path.mkdir()
    manifest = path / "manifest.json"
    manifest.write_text(
        json.dumps({"run_id": name, "status": "complete"}), encoding="utf-8"
    )
    (path / "payload.bin").write_bytes(b"x" * size)
    timestamp = time.time() - age_days * 86400
    os.utime(manifest, (timestamp, timestamp))
    return path


def test_retention_removes_only_recognized_old_sessions(tmp_path):
    old = _make_session(tmp_path, "run-old", 8, 10)
    recent = _make_session(tmp_path, "run-new", 0, 10)
    unrelated = tmp_path / "user-data"
    unrelated.mkdir()
    (unrelated / "important.zgs").write_bytes(b"data")

    removed = enforce_retention(tmp_path, max_age_days=7, max_bytes=100)

    assert old in removed
    assert not old.exists()
    assert recent.exists()
    assert (unrelated / "important.zgs").exists()


def test_system_sampler_degrades_without_proc_and_reports_current_process(tmp_path):
    current = process_sample(os.getpid())
    assert current["pid"] == os.getpid()
    values = sample_system(child_pids=[999999999], paths=[tmp_path])
    assert values["child_processes"][0]["alive"] is False
    assert values["disks"][str(tmp_path)]["free_bytes"] > 0


def test_new_recorder_marks_crashed_active_run_incomplete(tmp_path):
    old = tmp_path / "run-old"
    old.mkdir()
    manifest = old / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "run_id": "run-old",
                "status": "active",
                "latest_acquisition_id": "task-crashed",
            }
        ),
        encoding="utf-8",
    )
    recorder = DiagnosticRecorder(tmp_path)
    recorder.close()
    recovered = json.loads(manifest.read_text(encoding="utf-8"))
    assert recovered["status"] == "incomplete"
    assert recovered["recovered_on_next_start"]
