import json
from pathlib import Path
import zipfile

import numpy as np

from spectrometer.diagnostics.bundle_exporter import export_diagnostic_bundle
from spectrometer.diagnostics.recorder import DiagnosticRecorder


def _recording(tmp_path):
    recorder = DiagnosticRecorder(tmp_path / "diagnostics")
    recorder.start_acquisition("task-a")
    recorder.record_event("message", {"message": "采集正常"})
    recorder.record_sample({"raw_complete_frames": 10}, key="device-0")
    recorder.record_frame_summary({"device_id": 0, "sha256": "abc"})
    recorder.finish_acquisition("task-a", failed=False)
    recorder.close()
    return recorder


def test_default_bundle_contains_no_complete_spectral_data(tmp_path):
    recorder = _recording(tmp_path)
    startup_log = tmp_path / "startup.log"
    startup_log.write_text("token=private-value\nnormal line\n", encoding="utf-8")
    target = export_diagnostic_bundle(
        recorder.run_dir,
        tmp_path / "diagnostic.zip",
        acquisition_id="task-a",
        startup_log=startup_log,
    )
    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
        assert "bundle-manifest.json" in names
        assert "timeline.jsonl" in names
        assert not any(name.startswith("recent-frames/") for name in names)
        manifest = json.loads(archive.read("bundle-manifest.json"))
        assert manifest["recent_frames"] == "not_requested"
        assert manifest["scan_id"] == ""
        assert manifest["related_acquisition_ids"] == ["task-a"]
        log = archive.read("startup-log-tail.txt")
        assert b"private-value" not in log
        assert b"<redacted>" in log


def test_bundle_option_contains_at_most_last_ten_frames(tmp_path):
    recorder = _recording(tmp_path)
    frames = [
        {
            "device_id": 0,
            "packet_number": index,
            "source_pixel_count": 4,
            "pixel_bytes": np.arange(4, dtype=">u2").tobytes(),
        }
        for index in range(12)
    ]
    target = export_diagnostic_bundle(
        recorder.run_dir,
        tmp_path / "with-frames.zip",
        acquisition_id="task-a",
        include_recent_frames=True,
        recent_frames=frames,
    )
    with zipfile.ZipFile(target) as archive:
        matrix = np.load(
            __import__("io").BytesIO(archive.read("recent-frames/frames.npy")),
            allow_pickle=False,
        )
        metadata = json.loads(archive.read("recent-frames/metadata.json"))
        assert matrix.shape == (10, 4)
        assert metadata["frames"][0]["packet_number"] == 2
        assert metadata["frames"][-1]["packet_number"] == 11


def test_scan_bundle_includes_all_round_acquisitions_for_same_scan(tmp_path):
    recorder = DiagnosticRecorder(tmp_path / "diagnostics")
    recorder.record_event("global_before_scan", {"value": 1})
    for round_number, acquisition_id in ((1, "round-a"), (2, "round-b")):
        recorder.start_acquisition(acquisition_id)
        recorder.record_event(
            "motor_scan_acquisition_link",
            {
                "scan_id": "scan-123",
                "round_number": round_number,
                "acquisition_id": acquisition_id,
                "task_id": acquisition_id,
            },
        )
        recorder.record_event(
            "motor_scan_segment_started",
            {"scan_id": "scan-123", "round_number": round_number},
        )
        recorder.record_sample(
            {"round_number": round_number}, key=f"round-{round_number}"
        )
        recorder.record_frame_summary(
            {"device_id": 0, "round_number": round_number}
        )
        recorder.finish_acquisition(acquisition_id, failed=False)

    recorder.start_acquisition("unrelated")
    recorder.record_event("unrelated_event", {})
    recorder.record_sample({"round_number": 99}, key="unrelated")
    recorder.record_frame_summary({"device_id": 9, "round_number": 99})
    recorder.finish_acquisition("unrelated", failed=False)
    recorder.close()

    target = export_diagnostic_bundle(
        recorder.run_dir,
        tmp_path / "scan-diagnostic.zip",
        acquisition_id="round-b",
    )
    with zipfile.ZipFile(target) as archive:
        manifest = json.loads(archive.read("bundle-manifest.json"))
        assert manifest["scan_id"] == "scan-123"
        assert manifest["related_acquisition_ids"] == ["round-a", "round-b"]

        timeline = [
            json.loads(line)
            for line in archive.read("timeline.jsonl").splitlines()
        ]
        timeline_ids = {item.get("acquisition_id", "") for item in timeline}
        assert timeline_ids == {"", "round-a", "round-b"}
        assert not any(item["event"] == "unrelated_event" for item in timeline)

        performance = [
            json.loads(line)
            for line in archive.read("performance.jsonl").splitlines()
        ]
        assert {item["round_number"] for item in performance} == {1, 2}

        summaries = [
            json.loads(line)
            for line in archive.read("frame-summaries.jsonl").splitlines()
        ]
        assert {item["round_number"] for item in summaries} == {1, 2}
