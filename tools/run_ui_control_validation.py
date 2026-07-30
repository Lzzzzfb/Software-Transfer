"""Repeatable simulated end-to-end validation for acquisition UI control."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spectrometer.domain.enums import ControlState
from spectrometer.processing.references import ReferenceRepository
from spectrometer.qt import QT_API, QtWidgets
from spectrometer.ui.main_window import MainWindow


def wait_until(application, predicate, timeout=5.0, message="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(0.002)
    raise AssertionError(f"Timed out waiting for {message}")


def pump_events(application, duration=0.08):
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.002)


def validate(output_directory: Path):
    output_directory.mkdir(parents=True, exist_ok=True)
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(
        simulation=True,
        auto_start_simulation=False,
        settings_path=output_directory / "settings.json",
    )
    window.settings["storage_path"] = str(output_directory)
    window.storage_manager.set_output_directory(output_directory)
    window.reference_repository = ReferenceRepository(
        output_directory / "references"
    )
    rejected = []
    window.control.operation_rejected.connect(rejected.append)
    stop_call_ms = []
    try:
        assert window.settings["auto_store"] is False

        # Repeated global lifecycle: stop must return immediately while tail
        # quiet time and file finalization continue through Qt/background work.
        for _ in range(10):
            assert window.start_acquisition()
            assert window.control.global_state is ControlState.ACQUIRING
            pump_events(application, 0.03)
            started = time.perf_counter()
            assert window.stop_acquisition()
            stop_call_ms.append((time.perf_counter() - started) * 1000)
            wait_until(
                application,
                lambda: window.control.global_state is ControlState.IDLE,
                message="global idle",
            )

        # Four independent local tasks can overlap and stop out of order.
        for device_id in range(4):
            assert window._toggle_device_acquisition(device_id)
        assert all(
            window.control.device_state(device_id) is ControlState.ACQUIRING
            for device_id in range(4)
        )
        assert not window.start_acquisition()
        for device_id in (2, 0, 3, 1):
            assert window._toggle_device_acquisition(device_id)
            wait_until(
                application,
                lambda did=device_id: window.control.device_state(did)
                is ControlState.IDLE,
                message=f"local device {device_id} idle",
            )

        # Global ownership blocks card control and every reference operation.
        assert window.start_acquisition()
        assert not window.control.start_local(0)
        assert not window.capture_background()
        assert not window._capture_local_reference(0, "reference")
        assert window.stop_acquisition()
        wait_until(
            application,
            lambda: window.control.global_state is ControlState.IDLE,
            message="global guard scenario idle",
        )

        # Global and local single-shot tasks stop after their fresh frame(s).
        window.acquisition_mode.setCurrentIndex(1)
        assert window.start_acquisition()
        wait_until(
            application,
            lambda: window.control.global_state is ControlState.IDLE,
            message="global single shot",
        )
        assert window._toggle_device_acquisition(1)
        wait_until(
            application,
            lambda: window.control.device_state(1) is ControlState.IDLE,
            message="local single shot",
        )

        # One stored multi-device session validates human names and cleanup.
        window.acquisition_mode.setCurrentIndex(0)
        window.settings["batch_size"] = 3
        window.settings["auto_store"] = True
        assert window.start_acquisition()
        pump_events(application, 0.12)
        assert window.stop_acquisition()
        wait_until(
            application,
            lambda: window.control.global_state is ControlState.IDLE,
            timeout=10.0,
            message="stored session finalization",
        )
        csv_files = sorted(output_directory.glob("*.csv"))
        xlsx_files = sorted(output_directory.glob("*.xlsx"))
        assert len(csv_files) == 0
        assert len(xlsx_files) >= 1
        assert not list(output_directory.glob("*.part"))
        assert any("多设备_B000" in path.name for path in xlsx_files)

        # The same CSV + Excel option selects CSV for a one-device task.
        assert window._toggle_device_acquisition(0)
        pump_events(application, 0.08)
        assert window._toggle_device_acquisition(0)
        wait_until(
            application,
            lambda: window.control.device_state(0) is ControlState.IDLE,
            timeout=10.0,
            message="stored local session finalization",
        )
        csv_files = sorted(output_directory.glob("*.csv"))
        assert len(csv_files) >= 1
        assert any(
            "SNSIM-VIS-001_B000" in path.name for path in csv_files
        )
        assert not list(output_directory.glob("*.part"))
        window.settings["auto_store"] = False

        # Reference commands always use fresh single shots and do not enter
        # normal batch storage.
        assert window.capture_background()
        wait_until(
            application,
            lambda: window.control.global_state is ControlState.IDLE,
            message="global background",
        )
        assert window._capture_local_reference(0, "reference")
        wait_until(
            application,
            lambda: window.control.device_state(0) is ControlState.IDLE,
            message="local reference",
        )
        reference_files = list((output_directory / "references").glob("*.json"))
        assert len(reference_files) == 5

        report = {
            "qt_api": QT_API,
            "devices": len(window.device_manager.get_connected_devices()),
            "global_cycles": 10,
            "maximum_stop_call_ms": round(max(stop_call_ms), 3),
            "rejected_guard_requests": len(rejected),
            "csv_files": len(csv_files),
            "xlsx_files": len(xlsx_files),
            "reference_files": len(reference_files),
            "output_directory": str(output_directory.resolve()),
        }
        (output_directory / "validation-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return report
    finally:
        window.close()
        application.processEvents()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    output_directory = args.output_dir or (
        Path("data")
        / "validation"
        / f"ui-control-{datetime.now():%Y%m%d-%H%M%S}"
    )
    report = validate(output_directory)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
