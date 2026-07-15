"""ZGCAI 光谱仪工作站主界面。"""

from datetime import datetime
import math
from pathlib import Path
import shutil
import time

import numpy as np

from ..acquisition.coordinator import AcquisitionCoordinator
from ..communication.serial_port import DeviceFinder
from ..device.device_manager import DeviceManager
from ..domain.enums import AcquisitionMode, ProcessingMode, StorageFormat, SyncMode
from ..domain.models import AcquisitionSession, SpectrumFrame, SpectrumReference
from ..processing.formula import FormulaError, validate_formula
from ..processing.processor import ProcessingConfig, SpectrumProcessor
from ..processing.references import ReferenceRepository
from ..qt import QtCore, QtWidgets, dialog_exec
from ..services.settings_service import SettingsService
from ..storage.coordinator import BatchStorageCoordinator, StorageBackpressureError
from ..storage.csv_exporter import export_device_csv
from ..storage.recovery import scan_pending
from ..storage.spool import read_spool
from ..storage.xlsx_exporter import export_workbook
from .device_sidebar import DeviceSidebar
from .device_parameters import DeviceParametersDialog
from .diagnostics import DiagnosticsPanel
from .history_viewer import HistoryViewer
from .plot_widget import SpectrumPlotWidget
from .ribbon import MainRibbon
from .settings_dialog import SettingsDialog
from .status_panel import StatusPanel


class MainWindow(QtWidgets.QMainWindow):
    def __init__(
        self,
        simulation: bool = False,
        *,
        auto_start_simulation: bool = True,
        settings_path=None,
    ):
        super().__init__()
        self.simulation = simulation
        self.setWindowTitle("ZGCAI 光谱仪采集与分析工作站")
        self.resize(1480, 900); self.setMinimumSize(1100, 680)

        self.settings_service = SettingsService(settings_path)
        self.settings = self.settings_service.load()
        self.device_manager = DeviceManager()
        self.processor = SpectrumProcessor()
        self.acquisition = AcquisitionCoordinator(self._store_frame, display_fps=30)
        self.storage: BatchStorageCoordinator = None
        self._latest_frames = {}
        self._sim_x = {}; self._sim_sequence = {}; self._sim_phase = 0.0
        self._sim_running = False
        self._shown_since_status = 0; self._last_status_time = time.monotonic()
        self._last_formula_error = ""

        self._build_ui(); self._connect_signals(); self._apply_settings()
        self.reference_repository = ReferenceRepository(Path(self.settings["storage_path"]) / "references")
        QtCore.QTimer.singleShot(0, self._report_pending_recovery)

        self.plot_timer = QtCore.QTimer(self); self.plot_timer.timeout.connect(self._plot_tick); self.plot_timer.start(33)
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
        self.plot_widget = SpectrumPlotWidget()
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
        open_button = QtWidgets.QPushButton("打开历史"); open_button.clicked.connect(self.history_viewer_open); layout.addWidget(open_button)
        recover_button = QtWidgets.QPushButton("恢复缓存"); recover_button.clicked.connect(self.recover_spool); layout.addWidget(recover_button)
        save_image = QtWidgets.QPushButton("保存图片"); save_image.clicked.connect(self.save_plot_image); layout.addWidget(save_image)
        layout.addSpacing(16); layout.addWidget(QtWidgets.QLabel("处理"))
        self.display_mode = QtWidgets.QComboBox()
        for label, value in [("原始强度", "raw"), ("扣背景", "dark_subtract"), ("吸光度", "absorbance"), ("自定义公式", "custom")]:
            self.display_mode.addItem(label, value)
        self.display_mode.currentIndexChanged.connect(self._display_mode_changed); layout.addWidget(self.display_mode)
        self.formula_edit = QtWidgets.QLineEdit(); self.formula_edit.setPlaceholderText("例：-log10((I-Idark)/(I0-Idark))")
        self.formula_edit.setMinimumWidth(280); self.formula_edit.setVisible(False); self.formula_edit.editingFinished.connect(self._validate_formula)
        layout.addWidget(self.formula_edit, 1)
        layout.addWidget(QtWidgets.QLabel("X 轴")); self.x_axis = QtWidgets.QComboBox()
        self.x_axis.addItem("像素序号", "pixel"); self.x_axis.addItem("波长 (nm)", "wavelength"); layout.addWidget(self.x_axis)
        self.auto_range = QtWidgets.QCheckBox("自动缩放"); self.auto_range.setChecked(True)
        self.auto_range.toggled.connect(self.plot_widget.enable_auto_range); layout.addWidget(self.auto_range)
        clear = QtWidgets.QPushButton("清除对比"); clear.clicked.connect(self.plot_widget.clear_reference_curves); layout.addWidget(clear)
        return bar

    def _connect_signals(self):
        self.ribbon.start_requested.connect(self.start_acquisition)
        self.ribbon.stop_requested.connect(self.stop_acquisition)
        self.ribbon.background_requested.connect(self.capture_background)
        self.ribbon.reference_requested.connect(self.capture_reference)
        self.ribbon.sync_mode_changed.connect(self._sync_mode_changed)
        self.ribbon.discover_requested.connect(self.scan_devices)
        self.ribbon.new_history_requested.connect(lambda: self.history_viewer.new_page())
        self.ribbon.settings_requested.connect(self.open_settings)
        self.ribbon.help_requested.connect(self.show_help)
        self.ribbon.exit_requested.connect(self.close)
        self.sidebar.integration_changed.connect(self.device_manager.set_integration_time)
        self.sidebar.enabled_changed.connect(self._device_enabled_changed)
        self.sidebar.parameters_requested.connect(self.open_device_parameters)
        self.sidebar.connect_requested.connect(self.device_manager.add_and_connect)
        self.sidebar.disconnect_requested.connect(self.device_manager.remove_device)
        self.sidebar.refresh_button.clicked.connect(self.scan_devices)
        self.device_manager.device_added.connect(self._device_changed)
        self.device_manager.device_connected.connect(self._device_connected)
        self.device_manager.device_updated.connect(self._device_changed)
        self.device_manager.device_removed.connect(self._device_removed)
        self.device_manager.frame_arrived.connect(self._frame_arrived)
        self.device_manager.error_occurred.connect(lambda did, msg: self._log(f"设备 {did}: {msg}", "ERROR"))
        self.device_manager.device_connect_failed.connect(lambda did, msg: self._log(f"设备 {did} 连接失败: {msg}", "WARN"))
        self.device_manager.diagnostic_event.connect(self._log)

    def _apply_settings(self):
        self.sidebar.batch_size.setValue(self.settings["batch_size"])
        index = self.sidebar.storage_format.findData(self.settings["storage_format"])
        self.sidebar.storage_format.setCurrentIndex(max(0, index))
        self.sidebar.auto_store.setChecked(bool(self.settings["auto_store"]))
        self.plot_widget.set_line_width(self.settings["line_width"])
        index = self.display_mode.findData(self.settings["display_mode"]); self.display_mode.setCurrentIndex(max(0, index))
        index = self.x_axis.findData(self.settings["x_axis"]); self.x_axis.setCurrentIndex(max(0, index))

    def scan_devices(self):
        if self.simulation: return
        ports = DeviceFinder.list_available_ports()
        self.sidebar.set_available_ports([port["port_name"] for port in ports])
        existing = {device.port_name for device in self.device_manager.devices.values()}
        for port in ports:
            if port["port_name"] not in existing and DeviceFinder.is_likely_spectrometer(port):
                self.device_manager.add_and_connect(port["port_name"], 115200)
        self.status_panel.state_label.setText("设备扫描完成")

    def _device_changed(self, device_id):
        device = self.device_manager.get_device(device_id)
        if device: self.sidebar.add_or_update_device(device)

    def _device_connected(self, device_id):
        self._device_changed(device_id)
        device = self.device_manager.get_device(device_id)
        if not device or device.port_name.startswith("SIM"):
            return
        self.device_manager.init_device(device_id)
        QtCore.QTimer.singleShot(150, lambda: self.device_manager.query_version(device_id))
        QtCore.QTimer.singleShot(300, lambda: self.device_manager.query_calibration(device_id))
        QtCore.QTimer.singleShot(450, lambda: self.device_manager.query_serial_number(device_id))
        query_commands = [0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x38, 0x39, 0x3A, 0x3B]
        for index, command in enumerate(query_commands):
            QtCore.QTimer.singleShot(
                600 + index * 80,
                lambda value=command: self.device_manager.send_to_device(device_id, value),
            )

    def _device_removed(self, device_id):
        self.sidebar.remove_device(device_id); self.plot_widget.remove_device_curve(device_id)

    def _device_enabled_changed(self, device_id, enabled):
        device = self.device_manager.get_device(device_id)
        if device: device.enabled = enabled

    def _sync_mode_changed(self, mode):
        hard = mode in ("hard_internal", "hard_external")
        self.device_manager.global_sync_enabled = mode != "independent"
        self.device_manager.sync_mode = "hard" if hard else "soft"
        if mode == "hard_internal":
            self.device_manager.master_device_id = self.sidebar.selected_device_id
        elif mode == "hard_external":
            self.device_manager.master_device_id = None
        self._log(f"同步模式：{self.ribbon.sync_combo.currentText()}")

    def start_acquisition(self):
        devices = [device for device in self.device_manager.get_connected_devices() if device.enabled]
        if not devices:
            self.status_panel.state_label.setText("没有可采集设备"); self._log("没有可采集设备", "WARN"); return
        if self.sidebar.auto_store.isChecked() and self.storage is None:
            try: self._start_storage(devices)
            except Exception as exc: self._log(f"无法启动批量存储: {exc}", "ERROR"); return
        continuous = self.sidebar.acquisition_mode.currentIndex() == 0
        self.device_manager.start_sync_acquisition(continuous)
        self._sim_running = self.simulation
        self.status_panel.state_label.setText("连续采集中" if continuous else "单次采集中")
        self._log(f"开始{'连续' if continuous else '单次'}采集，共 {len(devices)} 台设备")

    def stop_acquisition(self):
        self._sim_running = False; self.device_manager.stop_all()
        self.status_panel.state_label.setText("正在排空存储队列…")
        self._close_storage(); self.status_panel.state_label.setText("已停止")
        self._log("采集停止，存储队列已排空")

    def _start_storage(self, devices):
        sync_value = self.ribbon.sync_combo.currentData()
        session = AcquisitionSession.create(
            [device.device_id for device in devices],
            mode=(AcquisitionMode.CONTINUOUS if self.sidebar.acquisition_mode.currentIndex() == 0 else AcquisitionMode.SINGLE),
            sync_mode=SyncMode(sync_value),
            storage_format=StorageFormat(self.sidebar.storage_format.currentData()),
            batch_size=self.sidebar.batch_size.value(),
        )
        wavelengths = {device.device_id: tuple(device.get_wavelength_array(device.info.valid_pixel or device.info.pixel_count or 4096)) for device in devices}
        labels = {device.device_id: (device.info.prod_serial or device.port_name or f"设备_{device.device_id}") for device in devices}
        metadata = {device.device_id: {"Port": device.port_name, "Serial": device.info.prod_serial, "Integration Time (us)": device.integration_time_us, "Trigger Mode": device.trigger_mode} for device in devices}
        self.storage = BatchStorageCoordinator(
            self.settings["storage_path"], session, wavelengths_by_device=wavelengths,
            device_labels=labels, device_metadata=metadata,
            warning_callback=lambda message: self._log(message, "WARN"),
            controlled_stop_callback=self._storage_stop_requested,
        )
        self.storage.start(); self._log(f"批量存储已启动：{session.batch_size} 帧/批，{session.storage_format.value}")

    def _store_frame(self, frame):
        if self.storage:
            self.storage.submit(frame)

    def _storage_stop_requested(self, message):
        self._log(message, "ERROR"); QtCore.QTimer.singleShot(0, self.stop_acquisition)

    def _close_storage(self):
        storage, self.storage = self.storage, None
        if storage:
            storage.close()
            for error in storage.errors: self._log(f"存储错误: {error}", "ERROR")
            if storage.exported_files: self._log(f"已生成 {len(storage.exported_files)} 个批量文件")

    def _frame_arrived(self, frame):
        self._latest_frames[frame.device_id] = frame
        try:
            observation = self.acquisition.ingest(frame)
            if observation.missing: self._log(f"设备 {frame.device_id} 检测到缺少 {observation.missing} 帧", "WARN")
        except StorageBackpressureError as exc:
            self._log(str(exc), "ERROR"); self.stop_acquisition()
        except Exception as exc:
            self._log(f"采集帧处理错误: {exc}", "ERROR")

    def _plot_tick(self):
        frames = self.acquisition.take_display_frames()
        for device_id, frame in frames.items():
            device = self.device_manager.get_device(device_id)
            if not device: continue
            wavelengths = device.get_wavelength_array(frame.pixel_count)
            mode = ProcessingMode(self.display_mode.currentData())
            config = ProcessingConfig(mode=mode, custom_formula=self.formula_edit.text().strip())
            try:
                processed = self.processor.process(
                    wavelengths, frame.pixels, config,
                    intensity_calibration=device.intensity_calib,
                    background=device.background_spectrum,
                    reference=device.reference_spectrum,
                )
            except (FormulaError, ValueError) as exc:
                message = str(exc)
                if message != self._last_formula_error:
                    self._last_formula_error = message; self._log(f"处理未应用: {message}", "WARN")
                continue
            x = np.arange(frame.pixel_count) if self.x_axis.currentData() == "pixel" else processed.wavelengths
            label = device.info.prod_serial or device.port_name or f"设备 {device_id}"
            self.plot_widget.update_device_curve(device_id, x, processed.values, label)
            self._shown_since_status += 1
            self.diagnostics.update_device(device_id, self.acquisition.diagnostics(device_id))
        self.plot_widget.set_axis_labels("像素序号" if self.x_axis.currentData() == "pixel" else "波长 (nm)", "吸光度" if self.display_mode.currentData() == "absorbance" else "强度 (counts)")

    def capture_background(self): self._capture_reference_kind("background")
    def capture_reference(self): self._capture_reference_kind("reference")

    def _capture_reference_kind(self, kind):
        device_id = self.sidebar.selected_device_id; frame = self._latest_frames.get(device_id)
        device = self.device_manager.get_device(device_id) if device_id is not None else None
        if not device or not frame: self._log("所选设备尚无可用光谱", "WARN"); return
        values = np.asarray(frame.pixels, dtype=np.float64)
        if kind == "background": device.background_spectrum = values.copy()
        else: device.reference_spectrum = values.copy()
        serial = device.info.prod_serial or f"PORT-{device.port_name}"
        reference = SpectrumReference.create(serial, kind, values)
        self.reference_repository.save(reference)
        self._log(f"设备 {device_id} 的{'背景' if kind == 'background' else '参考'}光谱已记录")

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
        dialog = SettingsDialog(self.settings, self)
        if dialog_exec(dialog):
            self.settings.update(dialog.values()); self.plot_widget.set_line_width(self.settings["line_width"])
            self.reference_repository = ReferenceRepository(Path(self.settings["storage_path"]) / "references")
            self.settings_service.save(self.settings); self._log("设置已保存")

    def open_device_parameters(self, device_id):
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

    def _update_status(self):
        now = time.monotonic(); elapsed = max(1e-6, now - self._last_status_time)
        fps = self._shown_since_status / elapsed; self._shown_since_status = 0; self._last_status_time = now
        missing = sum(self.acquisition.diagnostics(device_id).missing for device_id in self.device_manager.devices)
        queue_ratio = self.storage.queue_ratio if self.storage else 0.0
        try: free_gb = shutil.disk_usage(Path(self.settings["storage_path"]).resolve()).free / (1024 ** 3)
        except OSError: free_gb = 0.0
        self.status_panel.update_metrics(len(self.device_manager.get_connected_devices()), fps, missing, queue_ratio, free_gb)

    def _setup_simulation(self, auto_start):
        configurations = [("SIM1", "SIM-VIS-001", 350.0, 0.12), ("SIM2", "SIM-VIS-002", 500.0, 0.14), ("SIM3", "SIM-NIR-001", 850.0, 0.18), ("SIM4", "SIM-NIR-002", 1000.0, 0.20)]
        for port, serial, start, step in configurations:
            device_id = self.device_manager.add_simulated_device(port, serial, 4096, start, step)
            self._sim_x[device_id] = np.arange(4096, dtype=np.float64); self._sim_sequence[device_id] = 0
        self.simulation_timer = QtCore.QTimer(self); self.simulation_timer.timeout.connect(self._simulation_tick); self.simulation_timer.start(10)
        self._sim_running = bool(auto_start)
        if auto_start: QtCore.QTimer.singleShot(0, self.start_acquisition)
        self._log("模拟模式：4 台设备 × 4096 像素，目标 100 fps")

    def _simulation_tick(self):
        if not self._sim_running: return
        self._sim_phase += 0.045
        for device_id, x in self._sim_x.items():
            center = 900 + device_id * 560 + 180 * math.sin(self._sim_phase * (1 + device_id * 0.08))
            peak = 28000 * np.exp(-0.5 * ((x - center) / (85 + 14 * device_id)) ** 2)
            ripple = 1800 * np.sin(x / (48 + device_id * 9) + self._sim_phase * 2)
            pixels = np.clip(9000 + peak + ripple + device_id * 900, 0, 65535).astype(np.uint16)
            sequence = self._sim_sequence[device_id] & 0xFFFFFF; self._sim_sequence[device_id] = sequence + 1
            self.device_manager.inject_simulated_frame(SpectrumFrame.create(device_id, sequence << 8, pixels))

    def closeEvent(self, event):
        self._sim_running = False
        self.device_manager.stop_all(); self._close_storage()
        self.settings.update({"batch_size": self.sidebar.batch_size.value(), "storage_format": self.sidebar.storage_format.currentData(), "auto_store": self.sidebar.auto_store.isChecked(), "display_mode": self.display_mode.currentData(), "x_axis": self.x_axis.currentData()})
        try: self.settings_service.save(self.settings)
        except OSError as exc: self._log(f"设置保存失败: {exc}", "WARN")
        self.device_manager.remove_all_devices(); event.accept()
