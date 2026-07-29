"""ZGCAI 光谱仪工作站主界面。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import math
from pathlib import Path
import shutil
import time

import numpy as np

from ..acquisition.coordinator import AcquisitionCoordinator
from ..acquisition.controller import AcquisitionController
from ..communication.serial_port import DeviceFinder
from ..device.device_manager import DeviceManager
from ..diagnostics.bundle_exporter import export_diagnostic_bundle
from ..diagnostics.acquisition_observer import AcquisitionObserver
from ..diagnostics.recorder import DiagnosticRecorder
from ..diagnostics.paths import latest_run_with_acquisition
from ..diagnostics.retention import enforce_retention
from ..diagnostics.system_sampler import sample_system
from ..domain.enums import (
    AcquisitionMode,
    AcquisitionOwner,
    ControlState,
    ProcessingMode,
    StorageFormat,
    SyncMode,
    control_state_label,
)
from ..domain.models import SpectrumFrame, SpectrumReference
from ..processing.formula import FormulaError, validate_formula
from ..processing.processor import ProcessingSnapshot, SpectrumProcessor
from ..processing.references import ReferenceRepository
from ..qt import QtCore, QtWidgets, dialog_exec
from ..services.settings_service import SettingsService
from ..services.platform_paths import diagnostic_directory, log_directory
from ..storage.csv_exporter import export_device_csv
from ..storage.recovery import scan_pending
from ..storage.session_manager import StorageSessionManager
from ..storage.spool import read_spool
from ..storage.xlsx_exporter import export_workbook
from .device_sidebar import DeviceSidebar
from .device_parameters import DeviceParametersDialog
from .diagnostics import DiagnosticsPanel
from .history_viewer import HistoryViewer
from .input_controls import DirectDoubleSpinBox, NoWheelComboBox
from .plot_backend import create_spectrum_plot_widget
from .ribbon import MainRibbon
from .settings_dialog import SettingsDialog
from .status_panel import StatusPanel


DISPLAY_FPS = 20


class MainWindow(QtWidgets.QMainWindow):
    def __init__(
        self,
        simulation: bool = False,
        *,
        auto_start_simulation: bool = True,
        settings_path=None,
        port_allowlist=None,
    ):
        super().__init__()
        self.simulation = simulation
        self.port_allowlist = {str(port).upper() for port in (port_allowlist or [])}
        self.setWindowTitle("ZGCAI 光谱仪采集与分析工作站")
        self.resize(1480, 900); self.setMinimumSize(1100, 680)

        self.settings_service = SettingsService(settings_path)
        self.settings = self.settings_service.load()
        self._diagnostic_root = (
            Path(settings_path).parent / "diagnostics"
            if settings_path is not None
            else diagnostic_directory()
        )
        self.device_manager = DeviceManager()
        self.processor = SpectrumProcessor()
        self.display_fps = DISPLAY_FPS
        self.acquisition = AcquisitionCoordinator(
            lambda frame: None, display_fps=self.display_fps
        )
        self.reference_repository = ReferenceRepository(
            Path(self.settings["storage_path"]) / "references"
        )
        self.storage_manager = StorageSessionManager(
            self.settings["storage_path"],
            processing_snapshot_provider=self._processing_snapshots,
        )
        self.control = AcquisitionController(
            self.device_manager,
            self.storage_manager,
            reference_commit=self._commit_reference_frames,
        )
        self._initializing_devices = set()
        self._task_requests = {}
        self._processing_by_device = {}
        self._process_diagnostics = {}
        self._sim_x = {}; self._sim_sequence = {}; self._sim_phase = 0.0
        self._shown_since_status = 0; self._last_status_time = time.monotonic()
        self._last_formula_error = ""
        self._diagnostic_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="diagnostic-export"
        )
        self._diagnostic_future = None
        self._diagnostic_export_target = None
        self._diagnostic_recent_by_device = {}
        self._diagnostic_recent_expected = set()
        self._diagnostic_export_pending = False
        self._diagnostic_observer = AcquisitionObserver()
        self._pending_missing_logs = {}
        self._pending_plot_frames = {}

        self._build_ui()
        self.diagnostic_recorder = DiagnosticRecorder(
            self._diagnostic_root,
            warning=lambda message: self.diagnostics.append(message, "WARN")
        )
        try:
            enforce_retention(self._diagnostic_root)
        except OSError as exc:
            self.diagnostics.append(f"诊断记录清理失败：{exc}", "WARN")
        self.diagnostic_recorder.record_event(
            "plot_backend_selected",
            {
                "requested": self._plot_backend_info.requested,
                "active": self._plot_backend_info.active,
                "fallback_reason": self._plot_backend_info.fallback_reason,
            },
        )
        if self._plot_backend_info.fallback_reason:
            self.diagnostics.append(
                self._plot_backend_info.fallback_reason,
                "WARN",
            )
        self._connect_signals(); self._apply_settings()
        QtCore.QTimer.singleShot(0, self._report_pending_recovery)

        self.plot_timer = QtCore.QTimer(self)
        self.plot_timer.timeout.connect(self._plot_tick)
        self.plot_timer.start(round(1000 / self.display_fps))
        self.status_timer = QtCore.QTimer(self); self.status_timer.timeout.connect(self._update_status); self.status_timer.start(1000)

        if simulation:
            self._setup_simulation(auto_start_simulation)
        else:
            self.discovery_timer = QtCore.QTimer(self); self.discovery_timer.timeout.connect(self.scan_devices)
            self.discovery_timer.start(3000); QtCore.QTimer.singleShot(0, self.scan_devices)

    def _build_ui(self):
        central = QtWidgets.QWidget(); self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)
        self.ribbon = MainRibbon(); layout.addWidget(self.ribbon)
        self.plot_widget, self._plot_backend_info = create_spectrum_plot_widget()
        self.context_bar = self._build_context_bar(); layout.addWidget(self.context_bar)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.sidebar = DeviceSidebar(); splitter.addWidget(self.sidebar)
        self.tabs = QtWidgets.QTabWidget(); self.tabs.setObjectName("workspaceTabs")
        self.tabs.addTab(self.plot_widget, "实时光谱")
        self.history_viewer = HistoryViewer(); self.tabs.addTab(self.history_viewer, "历史数据")
        self.diagnostics = DiagnosticsPanel(); self.tabs.addTab(self.diagnostics, "诊断")
        splitter.addWidget(self.tabs); splitter.setStretchFactor(0, 0); splitter.setStretchFactor(1, 1)
        splitter.setSizes([315, 1150]); layout.addWidget(splitter, 1)
        self.status_panel = StatusPanel(); self.setStatusBar(self.status_panel)

    def _build_context_bar(self):
        bar = QtWidgets.QWidget(); bar.setObjectName("contextBar")
        layout = QtWidgets.QHBoxLayout(bar); layout.setContentsMargins(12, 5, 12, 5)
        open_button = QtWidgets.QPushButton("打开文件"); open_button.clicked.connect(self.history_viewer_open); layout.addWidget(open_button)
        recover_button = QtWidgets.QPushButton("恢复缓存"); recover_button.clicked.connect(self.recover_spool); layout.addWidget(recover_button)
        save_image = QtWidgets.QPushButton("保存图片"); save_image.clicked.connect(self.save_plot_image); layout.addWidget(save_image)
        layout.addSpacing(16); layout.addWidget(QtWidgets.QLabel("处理"))
        self.display_mode = NoWheelComboBox()
        for label, value in [("原始强度", "raw"), ("扣背景", "dark_subtract"), ("吸光度", "absorbance"), ("自定义公式", "custom")]:
            self.display_mode.addItem(label, value)
        self.display_mode.currentIndexChanged.connect(self._display_mode_changed); layout.addWidget(self.display_mode)
        self.formula_edit = QtWidgets.QLineEdit(); self.formula_edit.setPlaceholderText("例：-log10((I-Idark)/(I0-Idark))")
        self.formula_edit.setMinimumWidth(280); self.formula_edit.setVisible(False); self.formula_edit.editingFinished.connect(self._validate_formula)
        layout.addWidget(self.formula_edit, 1)
        layout.addWidget(QtWidgets.QLabel("X 轴")); self.x_axis = NoWheelComboBox()
        self.x_axis.addItem("像素序号", "pixel"); self.x_axis.addItem("波长 (nm)", "wavelength"); layout.addWidget(self.x_axis)
        self.auto_range = QtWidgets.QCheckBox("自动缩放"); self.auto_range.setChecked(True)
        self.auto_range.toggled.connect(self.plot_widget.enable_auto_range); layout.addWidget(self.auto_range)
        self.plot_widget.user_zoomed.connect(self._plot_user_zoomed)
        self.plot_widget.view_reset.connect(self._plot_view_reset)
        self.plot_widget.data_frame_painted.connect(self._plot_data_painted)
        self.fixed_y = QtWidgets.QCheckBox("固定 Y 轴")
        self.fixed_y.toggled.connect(self._fixed_y_toggled)
        layout.addWidget(self.fixed_y)
        layout.addWidget(QtWidgets.QLabel("最小"))
        self.y_minimum = DirectDoubleSpinBox(); self.y_minimum.setDecimals(6)
        self.y_minimum.setRange(-1e12, 1e12); self.y_minimum.setValue(0)
        self.y_minimum.setMaximumWidth(105); layout.addWidget(self.y_minimum)
        layout.addWidget(QtWidgets.QLabel("最大"))
        self.y_maximum = DirectDoubleSpinBox(); self.y_maximum.setDecimals(6)
        self.y_maximum.setRange(-1e12, 1e12); self.y_maximum.setValue(65535)
        self.y_maximum.setMaximumWidth(105); layout.addWidget(self.y_maximum)
        apply_y = QtWidgets.QPushButton("应用 Y 轴")
        apply_y.clicked.connect(self._apply_fixed_y_axis); layout.addWidget(apply_y)
        clear = QtWidgets.QPushButton("清除对比"); clear.clicked.connect(self.plot_widget.clear_reference_curves); layout.addWidget(clear)
        return bar

    def _plot_user_zoomed(self):
        self.auto_range.blockSignals(True)
        self.auto_range.setChecked(False)
        self.auto_range.blockSignals(False)

    def _plot_view_reset(self):
        self.auto_range.blockSignals(True)
        self.auto_range.setChecked(True)
        self.auto_range.blockSignals(False)

    def _plot_data_painted(self):
        self._shown_since_status += 1

    def _fixed_y_toggled(self, enabled):
        if enabled:
            self._apply_fixed_y_axis()
        else:
            self.plot_widget.disable_fixed_y()

    def _apply_fixed_y_axis(self):
        if not self.fixed_y.isChecked():
            self.plot_widget.disable_fixed_y()
            return True
        minimum = self.y_minimum.value()
        maximum = self.y_maximum.value()
        try:
            self.plot_widget.set_fixed_y_range(minimum, maximum)
        except ValueError as exc:
            self.fixed_y.blockSignals(True)
            self.fixed_y.setChecked(False)
            self.fixed_y.blockSignals(False)
            self.plot_widget.disable_fixed_y()
            self._operation_rejected(str(exc))
            return False
        self.status_panel.state_label.setText(
            "固定 Y 轴初始范围："
            f"{self.plot_widget.format_axis_value(minimum)} 至 "
            f"{self.plot_widget.format_axis_value(maximum)}"
        )
        return True

    def _connect_signals(self):
        self.ribbon.acquisition_requested.connect(self._toggle_acquisition)
        self.ribbon.background_requested.connect(self.capture_background)
        self.ribbon.reference_requested.connect(self.capture_reference)
        self.ribbon.sync_mode_changed.connect(self._sync_mode_changed)
        self.ribbon.discover_requested.connect(self.scan_devices)
        self.ribbon.settings_requested.connect(self.open_settings)
        self.ribbon.help_requested.connect(self.show_help)
        self.ribbon.exit_requested.connect(self.close)
        self.sidebar.integration_changed.connect(self._set_integration_time)
        self.sidebar.enabled_changed.connect(self._device_enabled_changed)
        self.sidebar.parameters_requested.connect(self.open_device_parameters)
        self.sidebar.acquisition_requested.connect(self._toggle_device_acquisition)
        self.sidebar.background_requested.connect(
            lambda device_id: self._capture_local_reference(device_id, "background")
        )
        self.sidebar.reference_requested.connect(
            lambda device_id: self._capture_local_reference(device_id, "reference")
        )
        self.sidebar.disconnect_requested.connect(self._remove_device)
        self.device_manager.device_added.connect(self._device_changed)
        self.device_manager.device_connected.connect(self._device_connected)
        self.device_manager.device_updated.connect(self._device_changed)
        self.device_manager.device_removed.connect(self._device_removed)
        self.device_manager.frame_arrived.connect(self._frame_arrived)
        self.device_manager.error_occurred.connect(lambda did, msg: self._log(f"设备 {did}: {msg}", "ERROR"))
        self.device_manager.device_connect_failed.connect(lambda did, msg: self._log(f"设备 {did} 连接失败: {msg}", "WARN"))
        self.device_manager.diagnostic_event.connect(self._log)
        self.device_manager.acquisition_diagnostics.connect(
            self._acquisition_diagnostics_updated
        )
        self.control.global_state_changed.connect(self._global_control_state_changed)
        self.control.device_state_changed.connect(self._device_control_state_changed)
        self.control.operation_rejected.connect(self._operation_rejected)
        self.control.diagnostic_event.connect(self._log)
        self.control.task_started.connect(self._control_task_started)
        self.control.task_finished.connect(self._control_task_finished)
        self.control.reference_captured.connect(self._reference_captured)
        self.diagnostics.export_requested.connect(self._export_diagnostic_bundle)
        self.device_manager.acquisition_frame_summary.connect(
            self._diagnostic_frame_summary
        )
        self.device_manager.recent_frames_ready.connect(
            self._diagnostic_recent_frames_ready
        )
        self.tabs.currentChanged.connect(self._diagnostic_tab_changed)

    def _apply_settings(self):
        self.sidebar.batch_size.setValue(self.settings["batch_size"])
        index = self.sidebar.storage_format.findData(self.settings["storage_format"])
        self.sidebar.storage_format.setCurrentIndex(max(0, index))
        # 自动存储是一次采集的主动选择，每次启动均保持未勾选。
        self.sidebar.auto_store.setChecked(False)
        self.plot_widget.set_line_width(self.settings["line_width"])
        index = self.display_mode.findData(self.settings["display_mode"]); self.display_mode.setCurrentIndex(max(0, index))
        index = self.x_axis.findData(self.settings["x_axis"]); self.x_axis.setCurrentIndex(max(0, index))

    def scan_devices(self):
        if self.simulation: return
        ports = DeviceFinder.list_available_ports()
        existing = {device.port_name for device in self.device_manager.devices.values()}
        for port in ports:
            allowed = not self.port_allowlist or port["port_name"].upper() in self.port_allowlist
            if allowed and port["port_name"] not in existing and DeviceFinder.is_likely_spectrometer(port):
                self.device_manager.add_and_connect(port["port_name"], 115200)

    def _device_changed(self, device_id):
        device = self.device_manager.get_device(device_id)
        if device: self.sidebar.add_or_update_device(device)

    def _device_connected(self, device_id):
        self._device_changed(device_id)
        device = self.device_manager.get_device(device_id)
        if device is not None:
            self.diagnostic_recorder.record_event(
                "device_connected",
                {"device_id": device_id, "port_name": device.port_name},
            )
        if not device or device.port_name.startswith("SIM"):
            return
        self._initializing_devices.add(device_id)
        self.status_panel.state_label.setText("设备初始化中…")
        self.device_manager.init_device(device_id)
        QtCore.QTimer.singleShot(150, lambda: self.device_manager.query_version(device_id))
        QtCore.QTimer.singleShot(300, lambda: self.device_manager.query_calibration(device_id))
        QtCore.QTimer.singleShot(450, lambda: self.device_manager.query_serial_number(device_id))
        # 0x39/0x3A require a U32 requested length and may return multi-KB blobs;
        # they are not runtime status queries and must not be sent with empty params.
        query_commands = [0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x38, 0x3B]
        for index, command in enumerate(query_commands):
            QtCore.QTimer.singleShot(
                600 + index * 80,
                lambda value=command: self.device_manager.send_to_device(device_id, value),
            )
        QtCore.QTimer.singleShot(1650, lambda: self._finish_device_initialization(device_id))

    def _finish_device_initialization(self, device_id):
        self._initializing_devices.discard(device_id)
        device = self.device_manager.get_device(device_id)
        if device is not None:
            self.diagnostic_recorder.record_event(
                "device_initialized",
                {
                    **device.to_dict(),
                    "serial_number": device.info.prod_serial,
                    "hardware_version": device.info.hw_ver,
                    "firmware_version": device.info.fw_ver,
                    "pixel_count": device.info.pixel_count,
                    "start_pixel": device.info.start_pixel,
                    "valid_pixel": device.info.valid_pixel,
                },
            )
        if not self._initializing_devices and not any(
            device.acquiring for device in self.device_manager.get_connected_devices()
        ):
            count = len(self.device_manager.get_connected_devices())
            self.status_panel.state_label.setText(f"就绪（{count} 台设备）")

    def _device_removed(self, device_id):
        self._initializing_devices.discard(device_id)
        self.diagnostic_recorder.record_event(
            "device_removed", {"device_id": device_id}
        )
        self.sidebar.remove_device(device_id); self.plot_widget.remove_device_curve(device_id)

    def _device_enabled_changed(self, device_id, enabled):
        device = self.device_manager.get_device(device_id)
        if device is None:
            return
        if self.control.global_state is not ControlState.IDLE:
            card = self.sidebar.cards.get(device_id)
            if card:
                card.enabled.blockSignals(True)
                card.enabled.setChecked(device.enabled)
                card.enabled.blockSignals(False)
            self._operation_rejected("总控任务未完全结束，暂时不能更改参与设备")
            return
        device.enabled = enabled

    def _set_integration_time(self, device_id, value):
        if not self.control.can_modify_device(device_id):
            device = self.device_manager.get_device(device_id)
            card = self.sidebar.cards.get(device_id)
            if device and card:
                card.integration.setValue(device.integration_time_us)
            self._operation_rejected("设备正在采集、停止或保存，暂时不能修改积分时间")
            return False
        return self.device_manager.set_integration_time(device_id, value)

    def _remove_device(self, device_id):
        if not self.control.can_remove_device(device_id):
            self._operation_rejected("采集任务未完全结束，暂时不能移除设备")
            return False
        self.device_manager.remove_device(device_id)
        return True

    def _sync_mode_changed(self, mode):
        self._log(f"同步模式：{self.ribbon.sync_combo.currentText()}")

    def start_acquisition(self):
        devices = self._global_devices()
        pending = [
            device.port_name
            for device in self.device_manager.get_connected_devices()
            if device.device_id in self._initializing_devices
        ]
        if pending:
            self._operation_rejected(f"设备正在初始化：{', '.join(pending)}")
            return False
        device_ids = [device.device_id for device in devices]
        started = self.control.start_global(
            device_ids,
            self._selected_acquisition_mode(),
            SyncMode(self.ribbon.sync_combo.currentData()),
            master_device_id=self.sidebar.selected_device_id,
            **self._storage_options(),
        )
        if started:
            self.acquisition.reset(device_ids)
        return started

    def stop_acquisition(self):
        return self.control.stop_global()

    def _toggle_acquisition(self):
        if self.control.global_state is not ControlState.IDLE:
            self.stop_acquisition()
        else:
            self.start_acquisition()

    def _toggle_device_acquisition(self, device_id):
        state = self.control.device_state(device_id)
        if state is ControlState.IDLE:
            started = self.control.start_local(
                device_id,
                self._selected_acquisition_mode(),
                **self._storage_options(),
            )
            if started:
                self.acquisition.reset([device_id])
            return started
        else:
            return self.control.stop_local(device_id)

    def _selected_acquisition_mode(self):
        return (
            AcquisitionMode.CONTINUOUS
            if self.sidebar.acquisition_mode.currentIndex() == 0
            else AcquisitionMode.SINGLE
        )

    def _storage_options(self):
        return {
            "auto_store": self.sidebar.auto_store.isChecked(),
            "storage_format": StorageFormat(
                self.sidebar.storage_format.currentData()
            ),
            "batch_size": self.sidebar.batch_size.value(),
        }

    def _global_devices(self):
        return [
            device
            for device in self.device_manager.get_connected_devices()
            if device.enabled and device.initialized
        ]

    def _frame_arrived(self, frame):
        try:
            self.control.on_frame(frame)
            observation = self.acquisition.ingest(frame)
            if observation.missing:
                self._pending_missing_logs[frame.device_id] = (
                    self._pending_missing_logs.get(frame.device_id, 0)
                    + observation.missing
                )
        except Exception as exc:
            self._log(f"采集帧处理错误: {exc}", "ERROR")

    def _processing_snapshots(self, devices):
        mode = ProcessingMode(self.display_mode.currentData())
        formula = self.formula_edit.text().strip()
        snapshots = {}
        for device in devices:
            pixel_count = device.info.valid_pixel or device.info.pixel_count or 4096
            calibration = (
                device.intensity_calib
                if device.intensity_calib is not None
                else ()
            )
            background = (
                device.background_spectrum
                if device.background_spectrum is not None
                else ()
            )
            reference = (
                device.reference_spectrum
                if device.reference_spectrum is not None
                else ()
            )
            snapshots[device.device_id] = ProcessingSnapshot(
                mode=mode.value,
                custom_formula=formula,
                wavelengths=tuple(device.get_wavelength_array(pixel_count)),
                intensity_calibration=tuple(float(value) for value in calibration),
                background=tuple(float(value) for value in background),
                reference=tuple(float(value) for value in reference),
            )
        self._processing_by_device.update(snapshots)
        return snapshots

    def _plot_tick(self):
        self._pending_plot_frames.update(self.acquisition.take_latest_frames())
        if self.tabs.currentWidget() is not self.plot_widget:
            return
        frames = self._pending_plot_frames
        self._pending_plot_frames = {}
        for device_id, frame in frames.items():
            device = self.device_manager.get_device(device_id)
            if not device: continue
            try:
                snapshot = self._processing_by_device.get(device_id)
                if snapshot is None:
                    snapshot = self._processing_snapshots([device])[device_id]
                processed = self.processor.process(
                    snapshot.wavelengths,
                    frame.pixels,
                    snapshot.config,
                    intensity_calibration=snapshot.intensity_calibration or None,
                    background=snapshot.background or None,
                    reference=snapshot.reference or None,
                )
            except (FormulaError, ValueError) as exc:
                message = str(exc)
                if message != self._last_formula_error:
                    self._last_formula_error = message; self._log(f"处理未应用: {message}", "WARN")
                continue
            x = (
                np.arange(frame.pixel_count)
                if self.x_axis.currentData() == "pixel"
                else np.asarray(snapshot.wavelengths)
            )
            label = device.info.prod_serial or device.port_name or f"设备 {device_id}"
            self.plot_widget.update_device_curve(device_id, x, processed.values, label)
            if device_id not in self._process_diagnostics:
                self.diagnostics.update_device(
                    device_id, self.acquisition.diagnostics(device_id)
                )
        y_labels = {
            "raw": "强度（计数）",
            "dark_subtract": "扣背景强度",
            "absorbance": "吸光度",
            "custom": "处理结果",
        }
        self.plot_widget.set_axis_labels(
            "像素序号" if self.x_axis.currentData() == "pixel" else "波长 (nm)",
            y_labels.get(self.display_mode.currentData(), "处理结果"),
        )

    def capture_background(self):
        return self._capture_global_reference("background")

    def capture_reference(self):
        return self._capture_global_reference("reference")

    def _capture_global_reference(self, kind):
        devices = self._global_devices()
        return self.control.capture_global_reference(
            [device.device_id for device in devices],
            kind,
            SyncMode(self.ribbon.sync_combo.currentData()),
            master_device_id=self.sidebar.selected_device_id,
        )

    def _capture_local_reference(self, device_id, kind):
        return self.control.capture_local_reference(device_id, kind)

    def _commit_reference_frames(self, kind, frames):
        references = []
        pending_values = {}
        for device_id, frame in frames.items():
            device = self.device_manager.get_device(device_id)
            if device is None:
                raise RuntimeError(f"设备 {device_id} 已断开，取消整批参考提交")
            values = np.asarray(frame.pixels, dtype=np.float64)
            serial = device.info.prod_serial or f"PORT-{device.port_name}"
            references.append(SpectrumReference.create(serial, kind, values))
            pending_values[device_id] = values
        self.reference_repository.save_batch(references)
        for device_id, values in pending_values.items():
            device = self.device_manager.get_device(device_id)
            if kind == "background":
                device.background_spectrum = values.copy()
            else:
                device.reference_spectrum = values.copy()

    def _reference_captured(self, kind, frames):
        label = "背景" if kind == "background" else "参考"
        self._log(f"{len(frames)} 台设备的{label}光谱已使用新采集帧记录")

    def _global_control_state_changed(self, state):
        self.ribbon.set_acquisition_state(ControlState(state).value)
        self._refresh_control_status()

    def _device_control_state_changed(self, device_id, state):
        value = ControlState(state)
        self.sidebar.set_device_control_state(device_id, value.value)
        self._refresh_control_status()

    def _refresh_control_status(self):
        if self.control.global_state is not ControlState.IDLE:
            self.status_panel.state_label.setText(
                f"总控：{control_state_label(self.control.global_state)}"
            )
            return
        active = [
            device_id
            for device_id in self.device_manager.devices
            if self.control.device_state(device_id) is not ControlState.IDLE
        ]
        if active:
            details = "、".join(
                f"{self.device_manager.get_device(device_id).port_name} "
                f"{control_state_label(self.control.device_state(device_id))}"
                for device_id in active
                if self.device_manager.get_device(device_id) is not None
            )
            self.status_panel.state_label.setText(f"单机任务：{details}")
        elif not self._initializing_devices:
            count = len(self.device_manager.get_connected_devices())
            self.status_panel.state_label.setText(f"就绪（{count} 台设备）")

    def _operation_rejected(self, message):
        self.status_panel.state_label.setText(message)
        self._log(message, "WARN")

    def _control_task_started(self, task_id, request):
        self._task_requests[task_id] = request
        self.diagnostic_recorder.start_acquisition(
            task_id,
            {
                "device_ids": list(request.device_ids),
                "owner": request.owner.value,
                "mode": request.mode.value,
                "sync_mode": request.sync_mode.value,
                "auto_store": request.auto_store,
            },
        )
        for device_id in request.device_ids:
            self._diagnostic_observer.reset(device_id)
        devices = [
            self.device_manager.get_device(device_id)
            for device_id in request.device_ids
        ]
        missing = [
            device
            for device in devices
            if device is not None and device.device_id not in self._processing_by_device
        ]
        if missing:
            self._processing_snapshots(missing)
        scope = "总控" if request.owner is AcquisitionOwner.GLOBAL else "单机"
        mode = "连续" if request.mode is AcquisitionMode.CONTINUOUS else "单次"
        self._log(f"{scope}{mode}采集已启动，共 {len(request.device_ids)} 台设备")

    def _control_task_finished(self, task_id, files, failed):
        request = self._task_requests.pop(task_id, None)
        if request is not None:
            for device_id in request.device_ids:
                self._processing_by_device.pop(device_id, None)
        if files:
            self._log(f"采集存储完成，已生成 {len(files)} 个文件")
        if failed:
            self._log("采集任务结束，但存在错误，详情请查看诊断记录", "ERROR")
        elif request is not None and request.owner is not AcquisitionOwner.CALIBRATION:
            self._log("采集任务已完全停止")
        self.diagnostic_recorder.finish_acquisition(
            task_id,
            failed=bool(failed),
            files=[str(path) for path in files],
            diagnostics=dict(self._process_diagnostics),
        )
        self._refresh_control_status()

    def _display_mode_changed(self):
        self.formula_edit.setVisible(self.display_mode.currentData() == "custom")
        self._last_formula_error = ""

    def _validate_formula(self):
        if self.display_mode.currentData() != "custom": return
        try:
            validate_formula(self.formula_edit.text()); self.status_panel.state_label.setText("公式有效")
        except FormulaError as exc:
            self.status_panel.state_label.setText(f"公式错误: {exc}"); self._log(f"公式错误: {exc}", "WARN")

    def open_settings(self):
        if self.control.busy:
            self._operation_rejected("采集任务未完全结束，暂时不能更改系统选项")
            return
        dialog = SettingsDialog(self.settings, self)
        if dialog_exec(dialog):
            self.settings.update(dialog.values()); self.plot_widget.set_line_width(self.settings["line_width"])
            self.storage_manager.set_output_directory(self.settings["storage_path"])
            self.reference_repository = ReferenceRepository(Path(self.settings["storage_path"]) / "references")
            self.settings_service.save(self.settings); self._log("设置已保存")

    def open_device_parameters(self, device_id):
        if not self.control.can_modify_device(device_id):
            self._operation_rejected("设备正在采集、停止或保存，暂时不能修改参数")
            return
        device = self.device_manager.get_device(device_id)
        if device:
            dialog_exec(DeviceParametersDialog(device, self.device_manager, self))

    def _report_pending_recovery(self):
        pending = scan_pending(self.settings["storage_path"])
        for path, recovery in pending.items():
            detail = f"发现未完成缓存 {path.name}：{len(recovery.frames)} 个完整帧"
            if recovery.issues:
                detail += f"，{recovery.issues[0].message}"
            self._log(detail, "WARN")

    def recover_spool(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择待恢复缓存", self.settings["storage_path"], "采集缓存 (*.part)"
        )
        if not path:
            return
        try:
            outputs, recovery = self._recover_spool_file(path)
            self._log(f"缓存恢复完成：{len(recovery.frames)} 帧，生成 {len(outputs)} 个文件")
            if recovery.issues:
                self._log(f"缓存尾部提示：{recovery.issues[0].message}", "WARN")
        except Exception as exc:
            self._log(f"缓存恢复失败: {exc}", "ERROR")

    def _recover_spool_file(self, path):
        recovery = read_spool(path)
        frames_by_device = {}
        for frame in recovery.frames:
            frames_by_device.setdefault(frame.device_id, []).append(frame)
        if not frames_by_device:
            raise ValueError("缓存中没有完整帧")
        source = Path(path); stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = source.parent / f"{source.stem}_recovered_{stamp}"
        outputs = []
        for device_id, frames in frames_by_device.items():
            target = Path(f"{prefix}_device_{device_id}.csv")
            export_device_csv(target, device_id, frames, metadata={"Recovered From": source.name})
            outputs.append(target)
        workbook = Path(f"{prefix}.xlsx")
        export_workbook(workbook, frames_by_device, session_metadata={"Recovered From": source.name})
        outputs.append(workbook)
        return outputs, recovery

    def history_viewer_open(self):
        self.tabs.setCurrentWidget(self.history_viewer); self.history_viewer.open_files()

    def save_plot_image(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "保存光谱图片", f"spectrum_{datetime.now():%Y%m%d_%H%M%S}.png", "PNG (*.png)")
        if path and self.plot_widget.save_image(path): self._log(f"光谱图片已保存: {path}")

    def show_help(self):
        QtWidgets.QMessageBox.information(self, "操作提示", "1. 查找并选择设备\n2. 选择采集、同步和批量存储方式\n3. 点击开始\n\n硬同步脉冲由下位机自动输出，上位机只负责布防和接收。")

    def _log(self, message, level="INFO"):
        self.diagnostics.append(message, level)
        if hasattr(self, "diagnostic_recorder"):
            self.diagnostic_recorder.record_event(
                "application_log", {"message": str(message)}, level=level
            )

    def _acquisition_diagnostics_updated(self, device_id, values):
        values = dict(values or {})
        self._process_diagnostics[device_id] = values
        self.diagnostics.update_acquisition(device_id, values)
        self.diagnostic_recorder.record_sample(
            self._diagnostic_observer.observe(device_id, values),
            key=f"acquisition-{device_id}",
        )

    def _update_status(self):
        now = time.monotonic(); elapsed = max(1e-6, now - self._last_status_time)
        fps = self._shown_since_status / elapsed; self._shown_since_status = 0; self._last_status_time = now
        pending_missing = self._pending_missing_logs
        self._pending_missing_logs = {}
        for device_id, count in pending_missing.items():
            self._log(
                f"设备 {device_id} 在最近 1 秒检测到缺少 {count} 帧", "WARN"
            )
        if self._process_diagnostics:
            missing = sum(
                values.get("missing_frames", 0)
                for values in self._process_diagnostics.values()
            )
        else:
            missing = sum(self.acquisition.diagnostics(device_id).missing for device_id in self.device_manager.devices)
        queue_ratio = self.storage_manager.queue_ratio
        try: free_gb = shutil.disk_usage(Path(self.settings["storage_path"]).resolve()).free / (1024 ** 3)
        except OSError: free_gb = 0.0
        self.status_panel.update_metrics(len(self.device_manager.get_connected_devices()), fps, missing, queue_ratio, free_gb)
        system_values = sample_system(
            child_pids=self.device_manager.acquisition_process_ids(),
            paths=[
                Path(self.settings["storage_path"]),
                self._diagnostic_root,
            ],
        )
        system_values["gui_display_fps"] = fps
        system_values["active_tab"] = self.tabs.tabText(self.tabs.currentIndex())
        system_values["plot_backend"] = self._plot_backend_info.active
        self.diagnostic_recorder.record_sample(
            system_values,
            key="system",
        )

    def _diagnostic_tab_changed(self, index):
        self.diagnostic_recorder.record_event(
            "workspace_tab_changed",
            {"index": int(index), "label": self.tabs.tabText(index)},
        )
        if self.tabs.widget(index) is self.plot_widget:
            QtCore.QTimer.singleShot(0, self._plot_tick)

    def _diagnostic_frame_summary(self, _device_id, values):
        self.diagnostic_recorder.record_frame_summary(dict(values or {}))

    def _export_diagnostic_bundle(self, include_recent_frames):
        if self._diagnostic_future is not None:
            return
        default = (
            f"zgcai-diagnostic-{datetime.now():%Y%m%d-%H%M%S}-"
            f"{self.diagnostic_recorder.run_id[-8:]}.zip"
        )
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "导出诊断包", str(Path.home() / default), "ZIP (*.zip)"
        )
        if not path:
            return
        self._diagnostic_export_target = Path(path)
        self._diagnostic_recent_by_device = {}
        self._diagnostic_export_pending = bool(include_recent_frames)
        self.diagnostics.set_export_state(True, "正在准备诊断快照…")
        if include_recent_frames and self.device_manager.request_recent_frames():
            self._diagnostic_recent_expected = set(self.device_manager.devices)
            QtCore.QTimer.singleShot(1000, self._start_diagnostic_export)
        else:
            self._diagnostic_recent_expected = set()
            self._start_diagnostic_export()

    def _diagnostic_recent_frames_ready(self, device_id, frames):
        self._diagnostic_recent_by_device[int(device_id)] = list(frames or [])
        if (
            self._diagnostic_export_pending
            and self._diagnostic_recent_expected
            and self._diagnostic_recent_expected.issubset(
                self._diagnostic_recent_by_device
            )
        ):
            self._start_diagnostic_export()

    def _start_diagnostic_export(self):
        if self._diagnostic_future is not None or self._diagnostic_export_target is None:
            return
        self._diagnostic_export_pending = False
        frames = []
        for values in self._diagnostic_recent_by_device.values():
            frames.extend(values)
        frames.sort(key=lambda item: item.get("monotonic_ns", 0))
        frames = frames[-10:]
        devices = {}
        for device_id, device in self.device_manager.devices.items():
            devices[device_id] = {
                **device.to_dict(),
                "serial_number": device.info.prod_serial,
                "hardware_version": device.info.hw_ver,
                "firmware_version": device.info.fw_ver,
                "pixel_count": device.info.pixel_count,
                "start_pixel": device.info.start_pixel,
                "valid_pixel": device.info.valid_pixel,
            }
        include = self.diagnostics.include_recent_frames.isChecked()
        self.diagnostics.set_export_state(True, "正在生成并校验 ZIP…")
        run_dir = self.diagnostic_recorder.run_dir
        acquisition_id = self.diagnostic_recorder.latest_acquisition_id
        if not acquisition_id:
            previous = latest_run_with_acquisition(
                self._diagnostic_root, exclude=run_dir
            )
            if previous is not None:
                run_dir, acquisition_id = previous
        self._diagnostic_future = self._diagnostic_executor.submit(
            export_diagnostic_bundle,
            run_dir,
            self._diagnostic_export_target,
            acquisition_id=acquisition_id,
            include_recent_frames=include,
            recent_frames=frames,
            startup_log=log_directory() / "startup.log",
            live_snapshot=bool(self.diagnostic_recorder.active_acquisition_id),
            device_snapshot=devices,
            acquisition_summary=dict(self._process_diagnostics),
        )
        QtCore.QTimer.singleShot(50, self._poll_diagnostic_export)

    def _poll_diagnostic_export(self):
        future = self._diagnostic_future
        if future is None:
            return
        if not future.done():
            QtCore.QTimer.singleShot(50, self._poll_diagnostic_export)
            return
        self._diagnostic_future = None
        try:
            path = future.result()
            self.diagnostics.set_export_state(False, f"已生成：{path}")
            self._log(f"诊断包已生成：{path}")
        except Exception as exc:
            self.diagnostics.set_export_state(False, f"导出失败：{exc}")
            self._log(f"诊断包导出失败：{exc}", "ERROR")
        finally:
            self._diagnostic_export_target = None

    def _setup_simulation(self, auto_start):
        configurations = [("SIM1", "SIM-VIS-001", 350.0, 0.12), ("SIM2", "SIM-VIS-002", 500.0, 0.14), ("SIM3", "SIM-NIR-001", 850.0, 0.18), ("SIM4", "SIM-NIR-002", 1000.0, 0.20)]
        for port, serial, start, step in configurations:
            device_id = self.device_manager.add_simulated_device(port, serial, 4096, start, step)
            self._sim_x[device_id] = np.arange(4096, dtype=np.float64); self._sim_sequence[device_id] = 0
        self.simulation_timer = QtCore.QTimer(self); self.simulation_timer.timeout.connect(self._simulation_tick); self.simulation_timer.start(10)
        if auto_start: QtCore.QTimer.singleShot(0, self.start_acquisition)
        self._log("模拟模式：4 台设备 × 4096 像素，目标 100 fps")

    def _simulation_tick(self):
        active_ids = {
            device_id
            for device_id in self._sim_x
            if self.control.device_state(device_id) is ControlState.ACQUIRING
        }
        if not active_ids:
            return
        self._sim_phase += 0.045
        for device_id, x in self._sim_x.items():
            if device_id not in active_ids:
                continue
            center = 900 + device_id * 560 + 180 * math.sin(self._sim_phase * (1 + device_id * 0.08))
            peak = 28000 * np.exp(-0.5 * ((x - center) / (85 + 14 * device_id)) ** 2)
            ripple = 1800 * np.sin(x / (48 + device_id * 9) + self._sim_phase * 2)
            pixels = np.clip(9000 + peak + ripple + device_id * 900, 0, 65535).astype(np.uint16)
            sequence = self._sim_sequence[device_id] & 0xFFFFFF; self._sim_sequence[device_id] = sequence + 1
            self.device_manager.inject_simulated_frame(SpectrumFrame.create(device_id, sequence << 8, pixels))

    def closeEvent(self, event):
        if self.control.global_state is not ControlState.IDLE:
            self.control.stop_global()
        else:
            for device_id in list(self.device_manager.devices):
                task = self.control.active_task_for_device(device_id)
                if task is not None and task.owner is AcquisitionOwner.LOCAL:
                    self.control.stop_local(device_id)
        self.control.finish_pending_stops()
        self.storage_manager.shutdown(timeout=10.0)
        self.settings.update({"batch_size": self.sidebar.batch_size.value(), "storage_format": self.sidebar.storage_format.currentData(), "auto_store": False, "display_mode": self.display_mode.currentData(), "x_axis": self.x_axis.currentData()})
        try: self.settings_service.save(self.settings)
        except OSError as exc: self._log(f"设置保存失败: {exc}", "WARN")
        self.device_manager.remove_all_devices()
        self.diagnostic_recorder.close(timeout=2.0)
        self._diagnostic_executor.shutdown(wait=False, cancel_futures=True)
        event.accept()
