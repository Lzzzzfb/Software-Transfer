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
