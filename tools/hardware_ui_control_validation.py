"""End-to-end validation of the current UI controller on real spectrometers."""

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
from spectrometer.processing.references import ReferenceRepository
from spectrometer.qt import QT_API, QtCore, QtWidgets
from spectrometer.ui.main_window import MainWindow
from spectrometer.ui.y_axis_dialog import YAxisSettings


def wait_until(application, predicate, timeout, description):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(0.002)
    raise TimeoutError(f"等待超时：{description}")


class HardwareValidation:
    def __init__(self, application, ports, output_directory, expected_devices):
        self.application = application
        self.ports = [port.upper() for port in ports]
        self.output_directory = output_directory
        self.expected_devices = expected_devices
        self.counts = Counter()
        self.errors = []
        self.events = []
        self.rejections = []
        self.stop_call_ms = []
        self.heartbeat_times = []
        self.stage = "initializing"
        self.window = MainWindow(
            simulation=False,
            settings_path=output_directory / "settings.json",
            port_allowlist=self.ports,
        )
        export_directory = output_directory / "exports"
        self.window.settings["storage_path"] = str(export_directory)
        self.window.storage_manager.set_output_directory(export_directory)
        self.window.reference_repository = ReferenceRepository(
            output_directory / "references"
        )
        self.window.settings["batch_size"] = 5
        self.window.device_manager.frame_arrived.connect(self.on_frame)
        self.window.device_manager.error_occurred.connect(
            lambda device_id, message: self.errors.append(
                f"设备 {device_id}: {message}"
            )
        )
        self.window.device_manager.device_connect_failed.connect(
            lambda device_id, message: self.errors.append(
                f"连接失败 {device_id}: {message}"
            )
        )
        self.window.device_manager.diagnostic_event.connect(self.events.append)
        self.window.control.operation_rejected.connect(self.rejections.append)
        self.heartbeat_timer = QtCore.QTimer()
        self.heartbeat_timer.timeout.connect(
            lambda: self.heartbeat_times.append((time.monotonic(), self.stage))
        )
        self.heartbeat_timer.start(20)
        self.window.show()

    def on_frame(self, frame):
        self.counts[frame.device_id] += 1

    @property
    def devices(self):
        return self.window.device_manager.get_connected_devices()

    @property
    def device_ids(self):
        return [device.device_id for device in self.devices]

    def wait_ready(self, timeout=35.0):
        wait_until(
            self.application,
            lambda: len(self.devices) == self.expected_devices
            and not self.window._initializing_devices
            and all(device.info.prod_serial for device in self.devices),
            timeout,
            f"{self.expected_devices} 台设备完成握手和初始化",
        )

    def wait_new_frames(self, before, frames_per_device=2, timeout=8.0):
        wait_until(
            self.application,
            lambda: all(
                self.counts[device_id] - before.get(device_id, 0)
                >= frames_per_device
                for device_id in self.device_ids
            ),
            timeout,
            f"每台设备新增 {frames_per_device} 帧",
        )

    def stop_global(self):
        started = time.perf_counter()
        if not self.window.stop_acquisition():
            raise AssertionError("总控停止请求被拒绝")
        self.stop_call_ms.append((time.perf_counter() - started) * 1000)
        wait_until(
            self.application,
            lambda: self.window.control.global_state is ControlState.IDLE,
            60.0,
            "总控停止和存储收尾",
        )

    def global_cycle(self, frames_per_device=2):
        before = dict(self.counts)
        if not self.window.start_acquisition():
            raise AssertionError("总控启动请求被拒绝")
        wait_until(
            self.application,
            lambda: self.window.control.global_state is ControlState.ACQUIRING,
            8.0,
            "总控进入采集",
        )
        self.wait_new_frames(before, frames_per_device)
        self.stop_global()

    def run(self, cycles):
        self.stage = "device_initialization"
        self.wait_ready()
        if self.window.settings["auto_store"]:
            raise AssertionError("自动存储启动默认值不是未勾选")
        self.events.append(f"识别完成：{len(self.devices)} 台")

        # Independent and software-synchronized global cycles.
        self.window.ribbon.sync_combo.setCurrentIndex(
            self.window.ribbon.sync_combo.findData("independent")
        )
        self.stage = "global_cycles"
        for _ in range(max(1, cycles - 1)):
            self.global_cycle()
        self.window.ribbon.sync_combo.setCurrentIndex(
            self.window.ribbon.sync_combo.findData("software")
        )
        self.global_cycle()

        # Real incoming frames must not move a manually zoomed fixed-Y view.
        self.stage = "fixed_y"
        self.window._apply_y_axis_settings(
            YAxisSettings(True, 0, 65535)
        )
        before = dict(self.counts)
        if not self.window.start_acquisition():
            raise AssertionError("固定 Y 场景总控启动失败")
        self.wait_new_frames(before, 2)
        x_min, x_max, _, _ = self.window.plot_widget._effective_range()
        self.window.plot_widget.set_view_range(x_min, x_max, 1000, 20000)
        held_range = self.window.plot_widget._effective_range()
        before = dict(self.counts)
        self.wait_new_frames(before, 2)
        if self.window.plot_widget._effective_range() != held_range:
            raise AssertionError("固定 Y 后新帧改变了人工缩放范围")
        self.window.plot_widget.reset_initial_view()
        if self.window.plot_widget._effective_range()[2:] != (0, 65535):
            raise AssertionError("双击复位模型未恢复固定 Y 初始范围")
        self.stop_global()
        self.window._apply_y_axis_settings(
            YAxisSettings(False, 0, 65535)
        )

        # Multiple local tasks overlap even while the top selection is sync.
        self.stage = "parallel_local"
        before = dict(self.counts)
        for device_id in self.device_ids:
            if not self.window._toggle_device_acquisition(device_id):
                raise AssertionError(f"设备 {device_id} 单机启动失败")
        wait_until(
            self.application,
            lambda: all(
                self.window.control.device_state(device_id)
                is ControlState.ACQUIRING
                for device_id in self.device_ids
            ),
            8.0,
            "所有单机任务进入采集",
        )
        self.wait_new_frames(before, 3)
        if self.window.start_acquisition():
            raise AssertionError("单机运行时错误接受了总控启动")
        if self.window.capture_background():
            raise AssertionError("单机运行时错误接受了背景采集")
        for device_id in reversed(self.device_ids):
            if not self.window._toggle_device_acquisition(device_id):
                raise AssertionError(f"设备 {device_id} 单机停止失败")
            wait_until(
                self.application,
                lambda did=device_id: self.window.control.device_state(did)
                is ControlState.IDLE,
                8.0,
                f"设备 {device_id} 单机停止",
            )

        # Global ownership blocks card and reference requests.
        self.stage = "global_guards"
        before = dict(self.counts)
        if not self.window.start_acquisition():
            raise AssertionError("守卫场景总控启动失败")
        self.wait_new_frames(before, 2)
        if self.window.control.start_local(self.device_ids[0]):
            raise AssertionError("总控期间错误接受了卡片启动")
        if self.window.capture_reference():
            raise AssertionError("总控期间错误接受了参考采集")
        self.stop_global()

        # A non-auto ordinary task must remain available for direct manual export.
        self.stage = "manual_spectrum_save"
        pending = self.window.control.pending_manual_capture
        if pending is None or not self.window.save_spectrum_button.isEnabled():
            raise AssertionError("普通采集停止后未生成可手动保存的上一任务缓存")
        previous_exports = set((self.output_directory / "exports").glob("*"))
        if not self.window._save_pending_spectrum():
            raise AssertionError("保存光谱请求被拒绝")
        wait_until(
            self.application,
            lambda: (
                not self.window.control.manual_export_active
                and self.window.control.pending_manual_capture is None
            ),
            60.0,
            "上一任务光谱后台导出",
        )
        new_exports = (
            set((self.output_directory / "exports").glob("*"))
            - previous_exports
        )
        if not any(
            path.suffix.lower() in {".csv", ".xlsx"}
            for path in new_exports
        ):
            raise AssertionError("保存光谱完成后未生成 CSV/Excel")

        # Fresh global background and per-device reference frames.
        self.stage = "references"
        if not self.window.capture_background():
            raise AssertionError("全局背景采集启动失败")
        wait_until(
            self.application,
            lambda: self.window.control.global_state is ControlState.IDLE,
            12.0,
            "全局背景提交",
        )
        if not all(device.background_spectrum is not None for device in self.devices):
            raise AssertionError("全局背景未覆盖全部设备")
        for device_id in self.device_ids:
            if not self.window._capture_local_reference(device_id, "reference"):
                raise AssertionError(f"设备 {device_id} 参考采集启动失败")
            wait_until(
                self.application,
                lambda did=device_id: self.window.control.device_state(did)
                is ControlState.IDLE,
                12.0,
                f"设备 {device_id} 参考提交",
            )

        # Stored tail batch through the same UI controller.
        self.stage = "stored_session"
        self.window.ribbon.sync_combo.setCurrentIndex(
            self.window.ribbon.sync_combo.findData("independent")
        )
        self.window.settings["auto_store"] = True
        self.global_cycle(frames_per_device=8)
        self.window.settings["auto_store"] = False

    def report(self, failure=None):
        self.stage = "report"
        self.application.processEvents()
        self.heartbeat_timer.stop()
        self.output_directory.mkdir(parents=True, exist_ok=True)
        screenshot_path = self.output_directory / "hardware_ui_control.png"
        screenshot_ok = self.window.grab().save(str(screenshot_path))
        heartbeat_gaps = [
            ((current[0] - previous[0]) * 1000, current[1])
            for previous, current in zip(
                self.heartbeat_times, self.heartbeat_times[1:]
            )
        ]
        gaps_by_stage = {}
        for gap, stage in heartbeat_gaps:
            gaps_by_stage[stage] = max(gap, gaps_by_stage.get(stage, 0.0))
        export_files = sorted(
            str(path)
            for path in (self.output_directory / "exports").glob("*")
            if path.is_file()
        )
        reference_files = sorted(
            str(path)
            for path in (self.output_directory / "references").glob("*.json")
        )
        result = {
            "passed": failure is None,
            "failure": str(failure) if failure else None,
            "qt_api": QT_API,
            "requested_ports": self.ports,
            "detected_devices": [
                {
                    "device_id": device.device_id,
                    "port": device.port_name,
                    "production_serial": device.info.prod_serial,
                    "firmware": device.info.fw_ver,
                    "valid_pixel": device.info.valid_pixel,
                    "integration_time_us": device.integration_time_us,
                    "frames": self.counts[device.device_id],
                    "diagnostics": self.window.acquisition.diagnostics(
                        device.device_id
                    ).__dict__,
                }
                for device in self.devices
            ],
            "maximum_stop_call_ms": (
                max(self.stop_call_ms) if self.stop_call_ms else None
            ),
            "maximum_gui_heartbeat_gap_ms": (
                max((gap for gap, _ in heartbeat_gaps), default=None)
            ),
            "maximum_gui_heartbeat_gap_ms_by_stage": gaps_by_stage,
            "rejected_guard_requests": len(self.rejections),
            "export_files": export_files,
            "reference_files": reference_files,
            "screenshot": str(screenshot_path),
            "screenshot_ok": bool(screenshot_ok),
            "events": self.events,
            "errors": self.errors,
        }
        report_path = self.output_directory / "hardware_ui_control.json"
        report_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return result

    def close(self):
        self.window.close()
        self.application.processEvents()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("ports", nargs="+")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-devices", type=int)
    parser.add_argument("--cycles", type=int, default=10)
    args = parser.parse_args(argv)
    application = QtWidgets.QApplication([])
    application.setStyle("Fusion")
    application.setStyleSheet(load_stylesheet())
    validation = HardwareValidation(
        application,
        args.ports,
        args.output_dir,
        args.expected_devices or len(args.ports),
    )
    failure = None
    try:
        validation.run(max(1, args.cycles))
    except Exception as exc:
        failure = exc
    result = validation.report(failure)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    validation.close()
    return 1 if failure or validation.errors else 0


if __name__ == "__main__":
    sys.exit(main())
