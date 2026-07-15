"""通过正式 DeviceManager/SerialWorker/存储链路进行三机连续采集验收。"""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from spectrometer.acquisition.coordinator import AcquisitionCoordinator
from spectrometer.device.device_manager import DeviceManager
from spectrometer.domain.enums import StorageFormat
from spectrometer.domain.models import AcquisitionSession
from spectrometer.qt import QtCore, application_exec
from spectrometer.storage.coordinator import BatchStorageCoordinator


class ValidationRun(QtCore.QObject):
    def __init__(self, application, ports, output_dir, target_frames, batch_size, timeout_seconds):
        super().__init__(); self.app = application; self.ports = ports; self.output_dir = output_dir
        self.target_frames = target_frames; self.batch_size = batch_size; self.timeout_seconds = timeout_seconds
        self.manager = DeviceManager(); self.device_ids = {}; self.counts = Counter(); self.first_sequences = {}; self.last_sequences = {}
        self.errors = []; self.events = []; self.storage = None; self.acquisition = None; self.started = False; self.finishing = False
        self.start_scheduled = False
        self.started_at = time.monotonic()
        self.manager.device_connected.connect(self.on_connected)
        self.manager.device_connect_failed.connect(lambda did, msg: self.errors.append(f"连接失败 {did}: {msg}"))
        self.manager.error_occurred.connect(lambda did, msg: self.errors.append(f"设备 {did}: {msg}"))
        self.manager.frame_arrived.connect(self.on_frame)
        self.manager.diagnostic_event.connect(self.events.append)

    def begin(self):
        for port in self.ports:
            device_id = self.manager.add_and_connect(port, 115200); self.device_ids[port] = device_id
        QtCore.QTimer.singleShot(int(self.timeout_seconds * 1000), lambda: self.finish("总超时"))

    def on_connected(self, device_id):
        self.events.append(f"设备 {device_id} 已连接")
        # Reproduce the production UI's paced initialization.  Real firmware drops
        # some back-to-back queries, especially the 233-byte 0x3C serial response.
        self.manager.init_device(device_id)
        QtCore.QTimer.singleShot(150, lambda did=device_id: self.manager.query_version(did))
        QtCore.QTimer.singleShot(300, lambda did=device_id: self.manager.query_calibration(did))
        QtCore.QTimer.singleShot(450, lambda did=device_id: self.manager.query_serial_number(did))
        QtCore.QTimer.singleShot(700, lambda did=device_id: self.manager.query_integration_time(did))
        QtCore.QTimer.singleShot(850, lambda did=device_id: self.manager.send_to_device(did, 0x33))
        if len(self.manager.get_connected_devices()) == len(self.ports) and not self.start_scheduled:
            self.start_scheduled = True
            QtCore.QTimer.singleShot(1500, self.start_acquisition)

    def start_acquisition(self):
        if self.started or self.finishing: return
        devices = self.manager.get_connected_devices()
        if (len(devices) != len(self.ports) or not all(device.initialized for device in devices)
                or not all(device.info.prod_serial for device in devices)):
            self.errors.append("设备信息未全部就绪"); self.finish("初始化失败"); return
        session = AcquisitionSession.create(
            [device.device_id for device in devices], batch_size=self.batch_size,
            storage_format=StorageFormat.CSV_EXCEL,
        )
        wavelengths = {device.device_id: tuple(device.get_wavelength_array(device.info.valid_pixel)) for device in devices}
        labels = {device.device_id: (device.info.prod_serial or device.port_name) for device in devices}
        metadata = {device.device_id: {"Port": device.port_name, "Serial": device.info.prod_serial,
                    "Pixel Count": device.info.valid_pixel, "Integration Time (us)": device.integration_time_us} for device in devices}
        self.storage = BatchStorageCoordinator(
            self.output_dir, session, wavelengths_by_device=wavelengths, device_labels=labels,
            device_metadata=metadata, warning_callback=lambda msg: self.events.append(f"WARN {msg}"),
            controlled_stop_callback=lambda msg: self.finish(msg),
        )
        self.storage.start(); self.acquisition = AcquisitionCoordinator(self.storage.submit, display_fps=30)
        self.started = True; self.manager.start_sync_acquisition(continuous=True)
        self.events.append("三机连续采集已启动")

    def on_frame(self, frame):
        if not self.started or self.finishing: return
        observation = self.acquisition.ingest(frame)
        self.counts[frame.device_id] += 1
        self.first_sequences.setdefault(frame.device_id, frame.sequence); self.last_sequences[frame.device_id] = frame.sequence
        if observation.missing: self.errors.append(f"设备 {frame.device_id} 缺少 {observation.missing} 帧")
        if all(self.counts[device_id] >= self.target_frames for device_id in self.device_ids.values()):
            self.finish("达到目标帧数")

    def finish(self, reason):
        if self.finishing: return
        self.finishing = True; self.events.append(reason); self.manager.stop_all()
        QtCore.QTimer.singleShot(300, self.finalize)

    def finalize(self):
        if self.storage:
            self.storage.close(); self.errors.extend(self.storage.errors)
        devices = list(self.manager.devices.values())
        result = {
            "ports": self.ports, "reason": self.events[-1] if self.events else "",
            "elapsed_seconds": time.monotonic() - self.started_at,
            "devices": {
                str(device.device_id): {
                    "port": device.port_name, "production_serial": device.info.prod_serial,
                    "pixel_count": device.info.pixel_count, "valid_pixel": device.info.valid_pixel,
                    "integration_time_us": device.integration_time_us,
                    "frames": self.counts[device.device_id],
                    "first_sequence": self.first_sequences.get(device.device_id),
                    "last_sequence": self.last_sequences.get(device.device_id),
                    "diagnostics": (self.acquisition.diagnostics(device.device_id).__dict__ if self.acquisition else {}),
                } for device in devices
            },
            "exported_files": [str(path) for path in (self.storage.exported_files if self.storage else [])],
            "spool_exists": bool(self.storage and self.storage.spool_path.exists()),
            "events": self.events, "errors": self.errors,
        }
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "continuous_validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        self.manager.remove_all_devices(); self.app.quit()


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("ports", nargs="+")
    parser.add_argument("--output-dir", default="data/validation/continuous")
    parser.add_argument("--frames", type=int, default=25); parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=20.0); args = parser.parse_args()
    app = QtCore.QCoreApplication([]); run = ValidationRun(app, args.ports, Path(args.output_dir), args.frames, args.batch_size, args.timeout)
    QtCore.QTimer.singleShot(0, run.begin); return application_exec(app)


if __name__ == "__main__": raise SystemExit(main())
