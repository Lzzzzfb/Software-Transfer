"""Validate DeviceManager close/reopen and automatic reconnect on real ports."""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spectrometer.device.device_manager import DeviceManager
from spectrometer.qt import QtCore, application_exec


class ReconnectValidation(QtCore.QObject):
    def __init__(self, app, ports, output, timeout_seconds):
        super().__init__()
        self.app = app
        self.ports = ports
        self.output = output
        self.manager = DeviceManager()
        self.connect_counts = Counter()
        self.errors = []
        self.events = []
        self.finished = False
        self.started_at = time.monotonic()
        self.manager.device_connected.connect(self.on_connected)
        self.manager.device_connect_failed.connect(
            lambda did, msg: self.errors.append(f"连接失败 {did}: {msg}")
        )
        self.manager.error_occurred.connect(
            lambda did, msg: self.errors.append(f"设备 {did}: {msg}")
        )
        self.manager.diagnostic_event.connect(self.events.append)
        for port in ports:
            self.manager.add_and_connect(port, 115200)
        QtCore.QTimer.singleShot(int(timeout_seconds * 1000), lambda: self.finalize("总超时"))

    def on_connected(self, device_id):
        self.connect_counts[device_id] += 1
        self.events.append(f"设备 {device_id} 第 {self.connect_counts[device_id]} 次连接成功")
        QtCore.QTimer.singleShot(100, lambda did=device_id: self.manager.query_version(did))
        if all(self.connect_counts[did] >= 1 for did in self.manager.devices) and max(self.connect_counts.values()) == 1:
            QtCore.QTimer.singleShot(500, self.simulate_disconnect)
        elif all(self.connect_counts[did] >= 2 for did in self.manager.devices):
            QtCore.QTimer.singleShot(500, lambda: self.finalize("重连成功"))

    def simulate_disconnect(self):
        self.events.append("关闭三个串口并触发自动重连")
        for device_id, worker in list(self.manager._workers.items()):
            QtCore.QMetaObject.invokeMethod(worker, "do_close", QtCore.Qt.QueuedConnection)
            self.manager._on_disconnect(device_id)

    def finalize(self, reason):
        if self.finished:
            return
        self.finished = True
        devices = list(self.manager.devices.values())
        result = {
            "reason": reason,
            "elapsed_seconds": time.monotonic() - self.started_at,
            "devices": {
                str(device.device_id): {
                    "port": device.port_name,
                    "connect_count": self.connect_counts[device.device_id],
                    "connected": device.connected,
                    "initialized": device.initialized,
                    "firmware": device.info.fw_ver,
                    "valid_pixel": device.info.valid_pixel,
                }
                for device in devices
            },
            "events": self.events,
            "errors": self.errors,
        }
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        self.manager.remove_all_devices()
        self.app.quit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ports", nargs="+")
    parser.add_argument("--output", default="data/validation/reconnect_validation.json")
    parser.add_argument("--timeout", type=float, default=12.0)
    args = parser.parse_args()
    app = QtCore.QCoreApplication([])
    run = ReconnectValidation(app, args.ports, Path(args.output), args.timeout)
    return application_exec(app)


if __name__ == "__main__":
    raise SystemExit(main())
