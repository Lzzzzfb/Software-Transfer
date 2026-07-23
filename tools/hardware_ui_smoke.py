"""Exercise the real three-device path through MainWindow without user input."""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main import load_stylesheet
from spectrometer.domain.enums import ControlState
from spectrometer.qt import QtCore, QtWidgets, application_exec
from spectrometer.ui.main_window import MainWindow


class UiHardwareSmoke(QtCore.QObject):
    def __init__(
        self, app, ports, output_dir, target_frames, timeout_seconds,
        expected_devices=None,
    ):
        super().__init__()
        self.app = app
        self.ports = ports
        self.output_dir = output_dir
        self.target_frames = target_frames
        self.expected_devices = expected_devices or len(ports)
        self.counts = Counter()
        self.started = False
        self.finishing = False
        self.started_at = time.monotonic()
        self.events = []
        self.errors = []
        self.exported_files = []
        self.storage_errors = []
        self.stop_call_ms = None
        self.stop_requested_at = None
        self.heartbeat_times = []

        self.window = MainWindow(
            simulation=False,
            settings_path=output_dir / "settings.json",
            port_allowlist=ports,
        )
        self.window.settings["storage_path"] = str(output_dir / "exports")
        self.window.storage_manager.set_output_directory(output_dir / "exports")
        self.window.sidebar.batch_size.setValue(5)
        self.window.sidebar.auto_store.setChecked(True)
        self.window.show()
        self.window.device_manager.frame_arrived.connect(self.on_frame)
        self.window.device_manager.error_occurred.connect(
            lambda did, message: self.errors.append(f"设备 {did}: {message}")
        )
        self.window.device_manager.device_connect_failed.connect(
            lambda did, message: self.errors.append(f"连接失败 {did}: {message}")
        )
        self.window.control.task_finished.connect(self.on_task_finished)
        self.window.control.operation_rejected.connect(
            lambda message: self.errors.append(f"操作被拒绝: {message}")
        )
        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.timeout.connect(self.poll_ready)
        self.poll_timer.start(100)
        self.heartbeat_timer = QtCore.QTimer(self)
        self.heartbeat_timer.timeout.connect(
            lambda: self.heartbeat_times.append(time.monotonic())
        )
        self.heartbeat_timer.start(20)
        QtCore.QTimer.singleShot(int(timeout_seconds * 1000), lambda: self.finish("总超时"))

    def poll_ready(self):
        if self.started or self.finishing:
            return
        devices = self.window.device_manager.get_connected_devices()
        if len(devices) != self.expected_devices:
            return
        if not all(device.initialized and device.info.prod_serial for device in devices):
            return
        if self.window._initializing_devices:
            return
        self.started = True
        self.events.append(
            f"正式主界面链路开始 {self.expected_devices} 台设备采集"
        )
        if not self.window.start_acquisition():
            self.finish("总控启动被拒绝")

    def on_frame(self, frame):
        if not self.started or self.finishing:
            return
        self.counts[frame.device_id] += 1
        devices = self.window.device_manager.get_connected_devices()
        if devices and all(self.counts[device.device_id] >= self.target_frames for device in devices):
            self.finish("达到目标帧数")

    def finish(self, reason):
        if self.finishing:
            return
        self.finishing = True
        self.stop_requested_at = time.monotonic()
        self.poll_timer.stop()
        self.events.append(reason)
        if self.window.control.global_state is not ControlState.IDLE:
            started = time.perf_counter()
            self.window.stop_acquisition()
            self.stop_call_ms = (time.perf_counter() - started) * 1000
        self.finalize_deadline = time.monotonic() + 60.0
        self.finalize_timer = QtCore.QTimer(self)
        self.finalize_timer.timeout.connect(self.poll_finalization)
        self.finalize_timer.start(25)
        self.poll_finalization()

    def on_task_finished(self, task_id, files, failed):
        self.exported_files.extend(str(path) for path in files)
        if failed:
            self.storage_errors.append(f"任务 {task_id} 报告失败")

    def poll_finalization(self):
        idle = self.window.control.global_state is ControlState.IDLE
        storage_idle = not self.window.storage_manager.active_device_ids
        if idle and storage_idle:
            self.finalize_timer.stop()
            self.finalize()
        elif time.monotonic() >= self.finalize_deadline:
            self.storage_errors.append("停止或存储收尾超过 60 秒")
            self.finalize_timer.stop()
            self.finalize()

    def finalize(self):
        self.heartbeat_timer.stop()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        screenshot = self.output_dir / "hardware_ui.png"
        screenshot_ok = self.window.grab().save(str(screenshot))
        devices = list(self.window.device_manager.devices.values())
        heartbeat_gaps = [
            (current - previous) * 1000
            for previous, current in zip(
                self.heartbeat_times, self.heartbeat_times[1:]
            )
        ]
        result = {
            "ports": self.ports,
            "elapsed_seconds": time.monotonic() - self.started_at,
            "status": self.window.status_panel.state_label.text(),
            "screenshot": str(screenshot),
            "screenshot_ok": bool(screenshot_ok),
            "stop_call_ms": self.stop_call_ms,
            "finalization_seconds": (
                time.monotonic() - self.stop_requested_at
                if self.stop_requested_at is not None
                else None
            ),
            "maximum_gui_heartbeat_gap_ms": (
                max(heartbeat_gaps) if heartbeat_gaps else None
            ),
            "devices": {
                str(device.device_id): {
                    "port": device.port_name,
                    "production_serial": device.info.prod_serial,
                    "valid_pixel": device.info.valid_pixel,
                    "integration_time_us": device.integration_time_us,
                    "frames": self.counts[device.device_id],
                    "diagnostics": self.window.acquisition.diagnostics(device.device_id).__dict__,
                }
                for device in devices
            },
            "exported_files": self.exported_files,
            "storage_errors": self.storage_errors,
            "events": self.events,
            "errors": self.errors,
        }
        result_path = self.output_dir / "hardware_ui_smoke.json"
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        self.window.close()
        self.app.quit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ports", nargs="+")
    parser.add_argument("--output-dir", default="data/validation/ui_hardware")
    parser.add_argument("--frames", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument(
        "--expected-devices",
        type=int,
        help="预期通过协议握手的设备数；默认等于端口数",
    )
    args = parser.parse_args()
    app = QtWidgets.QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(load_stylesheet())
    run = UiHardwareSmoke(
        app,
        args.ports,
        Path(args.output_dir),
        args.frames,
        args.timeout,
        args.expected_devices,
    )
    return application_exec(app)


if __name__ == "__main__":
    raise SystemExit(main())
