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
from spectrometer.qt import QtCore, QtWidgets, application_exec
from spectrometer.ui.main_window import MainWindow


class UiHardwareSmoke(QtCore.QObject):
    def __init__(self, app, ports, output_dir, target_frames, timeout_seconds):
        super().__init__()
        self.app = app
        self.ports = ports
        self.output_dir = output_dir
        self.target_frames = target_frames
        self.counts = Counter()
        self.started = False
        self.finishing = False
        self.started_at = time.monotonic()
        self.events = []
        self.errors = []
        self.storage = None

        self.window = MainWindow(
            simulation=False,
            settings_path=output_dir / "settings.json",
            port_allowlist=ports,
        )
        self.window.settings["storage_path"] = str(output_dir / "exports")
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
        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.timeout.connect(self.poll_ready)
        self.poll_timer.start(100)
        QtCore.QTimer.singleShot(int(timeout_seconds * 1000), lambda: self.finish("总超时"))

    def poll_ready(self):
        if self.started or self.finishing:
            return
        devices = self.window.device_manager.get_connected_devices()
        if len(devices) != len(self.ports):
            return
        if not all(device.initialized and device.info.prod_serial for device in devices):
            return
        if self.window._initializing_devices:
            return
        self.started = True
        self.events.append("正式主界面链路开始三机采集")
        self.window.start_acquisition()

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
        self.poll_timer.stop()
        self.events.append(reason)
        self.storage = self.window.storage
        self.window.stop_acquisition()
        QtCore.QTimer.singleShot(400, self.finalize)

    def finalize(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        screenshot = self.output_dir / "hardware_ui.png"
        screenshot_ok = self.window.grab().save(str(screenshot))
        devices = list(self.window.device_manager.devices.values())
        result = {
            "ports": self.ports,
            "elapsed_seconds": time.monotonic() - self.started_at,
            "status": self.window.status_panel.state_label.text(),
            "screenshot": str(screenshot),
            "screenshot_ok": bool(screenshot_ok),
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
            "exported_files": [str(path) for path in (self.storage.exported_files if self.storage else [])],
            "storage_errors": list(self.storage.errors if self.storage else []),
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
    args = parser.parse_args()
    app = QtWidgets.QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(load_stylesheet())
    run = UiHardwareSmoke(app, args.ports, Path(args.output_dir), args.frames, args.timeout)
    return application_exec(app)


if __name__ == "__main__":
    raise SystemExit(main())
