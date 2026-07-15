"""
主窗口 — 光谱仪上位机控制软件核心界面。
设备卡片式管理、实时可视化、数据存储。
"""

import os
import time
from datetime import datetime
from typing import Optional, Dict, List
import numpy as np

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QLabel, QComboBox, QCheckBox, QStatusBar,
    QMessageBox, QToolBar, QAction, QMenuBar, QMenu, QFileDialog,
    QDockWidget, QTabWidget, QGroupBox, QGridLayout, QLineEdit,
    QDialog, QFormLayout, QDialogButtonBox, QTextEdit, QDoubleSpinBox, QSpinBox
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal

from .device_panel import DeviceCardPanel
from .plot_widget import SpectrumPlotWidget, COLOR_PALETTE
from .settings_dialog import SettingsDialog, HistoryViewer
from ..device.device_manager import DeviceManager
from ..device.spectrometer import SpectrometerDevice
from ..acquisition.engine import AcquisitionEngine, DataProcessor
from ..storage.exporter import DataExporter
from ..communication.serial_port import DeviceFinder


SENSOR_TYPE_NAMES = {
    0: '未知',
    1: 'CCD (线阵)',
    2: 'CMOS (线阵)',
    3: 'InGaAs (NIR)',
    4: '背照式CCD',
    5: 'NMOS',
    6: 'PDA',
}

def sensor_type_name(n_type: int) -> str:
    return SENSOR_TYPE_NAMES.get(n_type, f'类型代码 {n_type}')


class MoreParamsDialog(QDialog):
    """设备更多参数窗口 — 校准系数可编辑"""

    def __init__(self, device, device_manager, parent=None):
        super().__init__(parent)
        self.device = device
        self.dm = device_manager
        self.setWindowTitle(f'设备 {device.device_id} 详细参数')
        self.setMinimumSize(520, 580)
        self._calib_spins = []  # 必须在 _make_calib_spin 之前初始化
        layout = QFormLayout(self)

        info = device.info
        layout.addRow('端口:', QLabel(device.port_name))
        layout.addRow('设备名称代号:', QLabel(str(info.name)))
        layout.addRow('传感器类型:', QLabel(f'{sensor_type_name(info.dev_type)} (0x{info.dev_type:08X})'))
        layout.addRow('硬件版本:', QLabel(f'{info.hw_ver:.2f}'))
        layout.addRow('固件版本:', QLabel(f'{info.fw_ver:.2f}'))
        layout.addRow('序列号:', QLabel(str(info.serial_num)))
        layout.addRow('总像素数:', QLabel(str(info.pixel_count)))
        layout.addRow('起始像素:', QLabel(str(info.start_pixel)))
        layout.addRow('有效像素:', QLabel(str(info.valid_pixel)))
        layout.addRow('曝光时间范围:', QLabel(f'{info.pos_time_min} - {info.pos_time_max} us'))
        layout.addRow('产品序列号:', QLabel(info.prod_serial or '未写入'))
        layout.addRow('', QLabel(''))
        # ---- 校准系数 ----
        layout.addRow('校准 C1 (0阶):', self._make_calib_spin(device.wavelength_calib.c1))
        layout.addRow('校准 C2 (1阶):', self._make_calib_spin(device.wavelength_calib.c2))
        layout.addRow('校准 C3 (2阶):', self._make_calib_spin(device.wavelength_calib.c3))
        layout.addRow('校准 C4 (3阶):', self._make_calib_spin(device.wavelength_calib.c4))
        self.btn_apply_calib = QPushButton('应用校准系数')
        self.btn_apply_calib.clicked.connect(self._on_apply_calib)
        layout.addRow(self.btn_apply_calib)
        layout.addRow('', QLabel(''))

        # ---- 触发模式 ----
        self._combo_trig = QComboBox()
        self._combo_trig.addItems(['软件触发', '软触发主机', '外部触发'])
        self._combo_trig.setCurrentIndex(device.trigger_mode)
        layout.addRow('触发模式:', self._combo_trig)
        self.btn_apply_trig = QPushButton('应用触发模式')
        self.btn_apply_trig.clicked.connect(self._on_apply_trig)
        layout.addRow(self.btn_apply_trig)
        layout.addRow('', QLabel(''))

        # ---- 其他参数 ----
        layout.addRow('增益 (0-63):', self._spin_value(device.gain, 0, 63))
        layout.addRow('采集间隔(us):', self._spin_value(device.interval_us, 0, 99999999))
        layout.addRow('触发延时(us):', self._spin_value(device.delay_us, 0, 99999999))
        layout.addRow('积分时间(us):', self._spin_value(device.integration_time_us, 1, 99999999))
        layout.addRow('平均次数:', self._spin_value(device.avg_count, 1, 10000))
        self.btn_apply_params = QPushButton('应用其他参数')
        self.btn_apply_params.clicked.connect(self._on_apply_params)
        layout.addRow(self.btn_apply_params)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addRow(buttons)

    def _make_calib_spin(self, value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(18)
        spin.setRange(-9999.0, 9999.0)
        spin.setValue(value)
        spin.setMinimumWidth(160)
        return spin

    def _spin_value(self, value: int, vmin: int, vmax: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(vmin, vmax)
        spin.setValue(value)
        spin.setMinimumWidth(140)
        return spin

    def _on_apply_calib(self):
        did = self.device.device_id
        c1 = self._find_widget('校准 C1'); c2 = self._find_widget('校准 C2')
        c3 = self._find_widget('校准 C3'); c4 = self._find_widget('校准 C4')
        if all([c1, c2, c3, c4]):
            self.dm.set_calib_coeff(did, c1.value(), c2.value(), c3.value(), c4.value())

    def _on_apply_trig(self):
        self.dm.set_trigger_mode(self.device.device_id, self._combo_trig.currentIndex())

    def _on_apply_params(self):
        did = self.device.device_id
        w = lambda t: self._find_widget(t)
        if w('增益'):       self.dm.set_gain(did, w('增益').value())
        if w('采集间隔'):   self.dm.send_to_device(did, 0x22, self._pack('<I', w('采集间隔').value()))
        if w('触发延时'):   self.dm.set_delay(did, w('触发延时').value())
        if w('积分时间'):   self.dm.set_integration_time(did, w('积分时间').value())
        if w('平均次数'):   self.dm.set_avg_count(did, w('平均次数').value())

    def _find_widget(self, text: str):
        lt = self.layout()
        for i in range(lt.rowCount()):
            item = lt.itemAt(i, QFormLayout.LabelRole)
            if item and item.widget():
                lbl = item.widget()
                if hasattr(lbl, 'text') and text in lbl.text():
                    field_item = lt.itemAt(i, QFormLayout.FieldRole)
                    if field_item and field_item.widget():
                        return field_item.widget()
        return None

    @staticmethod
    def _pack(fmt: str, *args) -> bytes:
        import struct
        return struct.pack(fmt, *args)


class MainWindow(QMainWindow):
    """光谱仪上位机主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle('ZGCAI 光谱仪控制软件')
        self.resize(1500, 900)
        self.setMinimumSize(1200, 720)

        # 核心组件
        self.device_manager = DeviceManager()
        self.acq_engine = AcquisitionEngine(self.device_manager)
        self.data_processor = DataProcessor(self.device_manager)
        self.data_exporter = DataExporter(self.device_manager)

        # 全局设置
        self.settings = {
            'storage_path': os.path.abspath('./data'),
            'auto_save': False,
            'save_mode': 'immediate',
            'save_param': 10,
            'merge_files': False,
            'line_width': 1.0,
            'auto_range': True,
        }

        # 显示模式
        self.display_mode = 'raw'
        self.custom_formula = ''
        self.x_axis_mode = 'pixel'

        # 历史查看器
        self.history_viewer: Optional[HistoryViewer] = None

        # 自动发现的端口缓存
        self._known_ports: set = set()

        # 设备验证超时定时器 (3秒内无版本响应则断开)
        self._verify_timers: dict = {}

        # 绘图节流: 每设备暂存最新一帧, 定时器 30fps 统一刷新
        self._staging: dict = {}  # device_id -> (x, y)

        self._plot_timer = QTimer(self)
        self._plot_timer.timeout.connect(self._on_plot_tick)
        self._plot_timer.start(33)  # ~30 fps

        # 构建UI
        self._setup_menu_bar()
        self._setup_central_widget()
        self._setup_status_bar()
        self._connect_signals()

        # 自动检测设备
        self._auto_detect_timer = QTimer(self)
        self._auto_detect_timer.timeout.connect(self._auto_detect_devices)
        self._auto_detect_timer.start(1500)  # 每1.5秒检测新端口
        self._auto_detect_devices()  # 立即执行一次

    # ==================== UI构建 ====================

    def _setup_menu_bar(self):
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu('文件(&F)')
        act_save = QAction('保存光谱数据...', self)
        act_save.setShortcut('Ctrl+S')
        act_save.triggered.connect(self._save_current_spectrum)
        file_menu.addAction(act_save)

        act_save_all = QAction('保存所有通道...', self)
        act_save_all.triggered.connect(self._save_all_channels)
        file_menu.addAction(act_save_all)

        file_menu.addSeparator()
        act_export_bg = QAction('导出背景光谱', self)
        act_export_bg.triggered.connect(self._export_background)
        file_menu.addAction(act_export_bg)
        act_export_ref = QAction('导出参考光谱', self)
        act_export_ref.triggered.connect(self._export_reference)
        file_menu.addAction(act_export_ref)

        file_menu.addSeparator()
        act_exit = QAction('退出(&X)', self)
        act_exit.setShortcut('Alt+F4')
        act_exit.triggered.connect(self.close)
        file_menu.addAction(act_exit)

        view_menu = menu_bar.addMenu('视图(&V)')
        act_history = QAction('历史数据查看器...', self)
        act_history.triggered.connect(self._open_history_viewer)
        view_menu.addAction(act_history)

        settings_menu = menu_bar.addMenu('设置(&S)')
        act_settings = QAction('系统设置...', self)
        act_settings.triggered.connect(self._open_settings)
        settings_menu.addAction(act_settings)

        help_menu = menu_bar.addMenu('帮助(&H)')
        act_about = QAction('关于...', self)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

    def _setup_central_widget(self):
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)

        # 左侧: 设备卡片面板
        self.device_panel = DeviceCardPanel()
        main_layout.addWidget(self.device_panel)

        # 右侧: 可视化 + 控制
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # 绘图区
        self.plot_widget = SpectrumPlotWidget()
        self.plot_widget.plot.getViewBox().sigRangeChangedManually.connect(
            self._on_user_zoom)

        # 模式/工具栏
        mode_bar = self._setup_mode_bar()
        right_layout.addLayout(mode_bar)

        right_layout.addWidget(self.plot_widget, stretch=1)
        main_layout.addWidget(right_widget, stretch=1)

    def _setup_mode_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(8)
        _lbl = 'color: #5f6368; font-weight: bold; font-size: 12px;'

        lbl_mode = QLabel('显示模式')
        lbl_mode.setStyleSheet(_lbl)
        bar.addWidget(lbl_mode)
        self.combo_display_mode = QComboBox()
        self.combo_display_mode.addItems(['原始光谱', '扣背景光谱', '吸收光谱', '自定义公式'])
        self.combo_display_mode.currentIndexChanged.connect(self._on_display_mode_changed)
        bar.addWidget(self.combo_display_mode)

        self.edit_custom_formula = QLineEdit()
        self.edit_custom_formula.setPlaceholderText('自定义公式 (例: log10(I0/I))')
        self.edit_custom_formula.setVisible(False)
        self.edit_custom_formula.textChanged.connect(self._on_custom_formula_changed)
        bar.addWidget(self.edit_custom_formula)

        lbl_x = QLabel('X轴')
        lbl_x.setStyleSheet(_lbl)
        bar.addWidget(lbl_x)
        self.combo_x_axis = QComboBox()
        self.combo_x_axis.addItems(['像素序号', '波长(nm)'])
        self.combo_x_axis.currentIndexChanged.connect(self._on_x_axis_changed)
        bar.addWidget(self.combo_x_axis)

        self.btn_save_data = QPushButton('保存光谱数据')
        self.btn_save_data.clicked.connect(self._save_current_spectrum)
        bar.addWidget(self.btn_save_data)

        self.btn_save_image = QPushButton('保存光谱图片')
        self.btn_save_image.clicked.connect(self._save_plot_image)
        bar.addWidget(self.btn_save_image)

        bar.addStretch()

        self.btn_rect_zoom = QPushButton('框选缩放')
        self.btn_rect_zoom.setCheckable(True)
        self.btn_rect_zoom.setChecked(False)
        self.btn_rect_zoom.toggled.connect(self._on_rect_zoom_toggled)
        bar.addWidget(self.btn_rect_zoom)

        self.chk_auto_range = QCheckBox('自动缩放')
        self.chk_auto_range.setChecked(True)
        self.chk_auto_range.toggled.connect(
            lambda v: self.plot_widget.plot.enableAutoRange() if v else None)
        bar.addWidget(self.chk_auto_range)

        self.btn_clear_ref = QPushButton('清除对比')
        self.btn_clear_ref.clicked.connect(self.plot_widget.clear_reference_curves)
        bar.addWidget(self.btn_clear_ref)

        # ---- 基线校正 (airPLS) ----
        bar.addSpacing(8)
        lbl_bl = QLabel('基线校正')
        lbl_bl.setStyleSheet(_lbl)
        bar.addWidget(lbl_bl)

        self.chk_baseline = QCheckBox('启用')
        self.chk_baseline.setToolTip('自适应迭代重加权惩罚最小二乘基线校正')
        self.chk_baseline.toggled.connect(self._on_baseline_toggled)
        bar.addWidget(self.chk_baseline)

        self.combo_bl_lam = QComboBox()
        self.combo_bl_lam.addItems(['1e3', '1e4', '1e5', '1e6', '1e7', '1e8'])
        self.combo_bl_lam.setCurrentIndex(2)  # 默认 1e5
        self.combo_bl_lam.setToolTip('平滑度参数 λ (越大基线越平滑)')
        self.combo_bl_lam.currentIndexChanged.connect(self._on_baseline_param_changed)
        bar.addWidget(self.combo_bl_lam)

        self.spin_bl_order = QSpinBox()
        self.spin_bl_order.setRange(1, 3)
        self.spin_bl_order.setValue(2)
        self.spin_bl_order.setToolTip('差分阶数 (1=线性, 2=曲率惩罚)')
        self.spin_bl_order.valueChanged.connect(self._on_baseline_param_changed)
        bar.addWidget(self.spin_bl_order)

        self.chk_show_bl = QCheckBox('显示基线')
        self.chk_show_bl.setToolTip('在图上叠加显示估计的基线')
        self.chk_show_bl.setEnabled(False)
        self.chk_show_bl.toggled.connect(self._on_show_baseline_toggled)
        bar.addWidget(self.chk_show_bl)

        self.lbl_frame_rate = QLabel('帧率: -- fps')
        self.lbl_frame_rate.setStyleSheet(
            'color: #1a73e8; font-weight: bold; font-size: 13px; '
            'background: #e8f0fe; padding: 4px 12px; border-radius: 4px;')
        bar.addWidget(self.lbl_frame_rate)

        self._frame_count = 0
        self._last_fps_time = time.time()

        return bar

    def _setup_status_bar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.lbl_connection = QLabel('设备: 自动检测中...')
        self.status_bar.addPermanentWidget(self.lbl_connection)

    # ==================== 信号连接 ====================

    def _connect_signals(self):
        # 卡片面板 → MainWindow
        self.device_panel.bg_requested.connect(self._on_bg_for_device)
        self.device_panel.ref_requested.connect(self._on_ref_for_device)
        self.device_panel.acq_start_requested.connect(self._on_acq_start_for_device)
        self.device_panel.acq_stop_requested.connect(self._on_acq_stop_for_device)
        self.device_panel.integ_time_requested.connect(self._on_set_integ_for_device)
        self.device_panel.avg_count_requested.connect(self._on_set_avg_for_device)
        self.device_panel.enable_toggled.connect(self._on_device_enable_toggled)
        self.device_panel.more_params_requested.connect(self._on_more_params_for_device)
        self.device_panel.close_requested.connect(self._on_close_device)

        # 总控按钮 (所有设备)
        self.device_panel.master_start_requested.connect(self._on_master_start)
        self.device_panel.master_stop_requested.connect(self._on_master_stop)

        # 设备管理器 → MainWindow
        self.device_manager.device_removed.connect(self._on_device_removed)
        self.device_manager.device_updated.connect(self._on_device_updated)
        self.device_manager.device_connected.connect(self._on_device_connected)
        self.device_manager.device_connect_failed.connect(self._on_device_connect_failed)
        self.device_manager.calib_ready.connect(self._on_calib_ready)
        self.device_manager.data_arrived.connect(self._on_data_arrived)
        self.device_manager.error_occurred.connect(self._on_device_error)

        # 采集引擎 → MainWindow
        self.acq_engine.device_acq_started.connect(self._on_device_acq_started)
        self.acq_engine.device_acq_stopped.connect(self._on_device_acq_stopped)
        self.acq_engine.frame_collected.connect(self._on_frame_collected)

    # ==================== 自动检测设备 ====================

    def _auto_detect_devices(self):
        """扫描全部 COM 口尝试连接光谱仪"""
        ports = DeviceFinder.list_available_ports()
        for p in ports:
            port_name = p['port_name']
            if port_name in self._known_ports:
                continue
            self._known_ports.add(port_name)
            # 全部尝试连接（非阻塞：线程中打开）
            self.device_manager.add_and_connect(port_name, 115200)

    # ==================== 设备连接 ====================

    def _on_device_connected(self, device_id: int):
        device = self.device_manager.get_device(device_id)
        if not device:
            return
        if device_id not in self.device_panel.cards:
            self.device_panel.add_card(device_id, device)
        self.status_bar.showMessage(f'{device.port_name} 已连接 (设备{device_id})', 3000)
        self.device_manager.init_device(device_id)
        did = device_id
        QTimer.singleShot(200, lambda: self.device_manager.query_version(did))
        QTimer.singleShot(500, lambda: self.device_manager.query_calibration(did))
        QTimer.singleShot(800, lambda: self.device_manager.query_serial_number(did))

        # 3秒验证超时: 收不到版本响应则断开(不是光谱仪)
        self._cancel_verify_timer(device_id)
        t = QTimer(self)
        t.setSingleShot(True)
        t.timeout.connect(lambda: self._on_verify_timeout(did))
        t.start(3000)
        self._verify_timers[device_id] = t

    def _cancel_verify_timer(self, device_id: int):
        t = self._verify_timers.pop(device_id, None)
        if t:
            t.stop()

    def _on_verify_timeout(self, device_id: int):
        """版本查询超时 — 不是光谱仪，断开连接"""
        device = self.device_manager.get_device(device_id)
        if device and not device.initialized:
            self.status_bar.showMessage(f'{device.port_name} 无响应, 已断开', 3000)
            self.device_manager.remove_device(device_id)

    def _on_device_connect_failed(self, device_id: int, error_msg: str):
        device = self.device_manager.get_device(device_id)
        port = device.port_name if device else f'设备{device_id}'
        # 静默失败（自动检测时很多端口不是光谱仪）
        self.device_manager.remove_device(device_id)

    def _on_device_removed(self, device_id: int):
        self.device_panel.remove_card(device_id)
        self._known_ports.discard(
            self.device_manager.get_device(device_id).port_name
            if self.device_manager.get_device(device_id) else '')
        if device_id in self.plot_widget.device_curves:
            curve = self.plot_widget.device_curves[device_id]
            name = curve.opts.get('name', '')
            if name:
                self.plot_widget.legend.removeItem(name)
            self.plot_widget.plot.removeItem(curve)
            del self.plot_widget.device_curves[device_id]

    def _on_device_updated(self, device_id: int):
        device = self.device_manager.get_device(device_id)
        if device:
            self.device_panel.update_card(device_id, device)
            # 设备已初始化(收到版本信息) → 取消验证超时
            if device.initialized:
                self._cancel_verify_timer(device_id)

    def _on_close_device(self, device_id: int):
        if self.acq_engine.is_device_running(device_id):
            self.acq_engine.stop_device(device_id)
        self.device_manager.remove_device(device_id)

    def _on_master_start(self, continuous: bool):
        """总控按钮 — 启动所有已启用设备"""
        enabled_ids = [did for did, dev in self.device_manager.devices.items()
                       if dev.enabled and dev.connected]
        if enabled_ids:
            self.acq_engine.start(continuous=continuous, device_ids=enabled_ids)

    def _on_master_stop(self):
        """总控按钮 — 停止所有设备"""
        self.acq_engine.stop()

    # ==================== 卡片按钮操作 ====================

    def _on_bg_for_device(self, device_id: int):
        device = self.device_manager.get_device(device_id)
        if device and device.latest_pixels is not None:
            self.data_processor.store_background(device_id)
            self.data_exporter.export_background(device)
            self.status_bar.showMessage(f'设备{device_id} 背景光谱已存储', 2000)

    def _on_ref_for_device(self, device_id: int):
        device = self.device_manager.get_device(device_id)
        if device and device.latest_pixels is not None:
            self.data_processor.store_reference(device_id)
            self.data_exporter.export_reference(device)
            self.status_bar.showMessage(f'设备{device_id} 参考光谱已存储', 2000)

    def _on_acq_start_for_device(self, device_id: int, continuous: bool):
        if not self.device_panel.get_card(device_id):
            return
        card = self.device_panel.get_card(device_id)
        if not card or not card.is_enabled():
            return
        self.acq_engine.start_device(device_id, continuous)

    def _on_acq_stop_for_device(self, device_id: int):
        self.acq_engine.stop_device(device_id)

    def _on_set_integ_for_device(self, device_id: int, time_us: int):
        self.device_manager.set_integration_time(device_id, time_us)

    def _on_set_avg_for_device(self, device_id: int, count: int):
        self.device_manager.set_avg_count(device_id, count)

    def _on_device_enable_toggled(self, device_id: int, enabled: bool):
        device = self.device_manager.get_device(device_id)
        if device:
            device.enabled = enabled
        # 禁用时停止采集
        if not enabled and self.acq_engine.is_device_running(device_id):
            self.acq_engine.stop_device(device_id)

    def _on_more_params_for_device(self, device_id: int):
        device = self.device_manager.get_device(device_id)
        if device:
            dlg = MoreParamsDialog(device, self.device_manager, self)
            dlg.exec_()

    # ==================== 采集状态 ====================

    def _on_device_acq_started(self, device_id: int):
        self.device_panel.set_card_acquiring(device_id, True)
        self.device_panel.set_master_acquiring(True)

    def _on_device_acq_stopped(self, device_id: int):
        self.device_panel.set_card_acquiring(device_id, False)
        if not self.acq_engine.is_running():
            self.device_panel.set_master_acquiring(False)

    def _on_frame_collected(self, device_id: int, frame_count: int):
        self._frame_count += 1
        now = time.time()
        elapsed = now - self._last_fps_time
        if elapsed >= 0.5:
            fps = self._frame_count / elapsed
            self.lbl_frame_rate.setText(f'帧率: {fps:.1f} fps')
            self._frame_count = 0
            self._last_fps_time = now

    # ==================== 数据显示 ====================

    def _get_x_axis_data(self, device) -> np.ndarray:
        if self.x_axis_mode == 'wavelength' and device.latest_wavelengths is not None:
            return device.latest_wavelengths.copy()
        if device.latest_pixels is not None:
            return np.arange(len(device.latest_pixels), dtype=np.float64)
        return np.array([])

    def _on_data_arrived(self, device_id: int, pixel_indices: np.ndarray,
                         pixels: np.ndarray):
        """收到数据 — 暂存, 由 _on_plot_tick 批量绘图 (一次 autoRange)"""
        device = self.device_manager.get_device(device_id)
        if not device or not device.enabled:
            return
        mode_map = {'原始光谱': 'raw', '扣背景光谱': 'dark_subtract',
                    '吸收光谱': 'absorbance', '自定义公式': 'custom'}
        mode_str = mode_map.get(self.combo_display_mode.currentText(), 'raw')
        result = device.get_display_spectrum(mode_str, self.custom_formula)
        if result is not None:
            _, y = result
            x = self._get_x_axis_data(device)
            self._staging[device_id] = (x, y)

    def _on_plot_tick(self):
        """30fps 批量绘图 — 所有暂存设备一次 setData + 一次 autoRange"""
        if not self._staging:
            return
        staging = dict(self._staging)
        self._staging.clear()
        auto = self.chk_auto_range.isChecked()
        for did, (x, y) in staging.items():
            dev = self.device_manager.get_device(did)
            if not dev:
                continue
            sn = dev.info.prod_serial or f'SN{dev.info.serial_num}' if dev.info.serial_num else f'Ch{did}'
            self.plot_widget.update_device_curve(did, x, y, sn)
        self._update_baseline_curves()
        if auto:
            self.plot_widget.plot.enableAutoRange()

    def _on_x_axis_changed(self, idx: int):
        self.x_axis_mode = 'wavelength' if idx == 1 else 'pixel'
        label = '波长' if idx == 1 else '像素序号'
        self.plot_widget.plot.setLabel('bottom', label, units='nm' if idx == 1 else '')
        self._refresh_all_curves()

    def _on_rect_zoom_toggled(self, checked: bool):
        self.plot_widget.set_rect_zoom_mode(checked)
        if checked:
            self.btn_rect_zoom.setText('框选缩放 (开)')
        else:
            self.btn_rect_zoom.setText('框选缩放')

    def _on_user_zoom(self, *args):
        if self.chk_auto_range.isChecked():
            self.chk_auto_range.setChecked(False)

    def _on_display_mode_changed(self, idx: int):
        self.edit_custom_formula.setVisible(idx == 3)
        mode_map = {0: 'raw', 1: 'dark_subtract', 2: 'absorbance', 3: 'custom'}
        self.display_mode = mode_map.get(idx, 'raw')
        self._refresh_all_curves()

    def _on_custom_formula_changed(self, text: str):
        self.custom_formula = text
        if self.display_mode == 'custom':
            self._refresh_all_curves()

    # ==================== 基线校正 ====================

    def _on_baseline_toggled(self, enabled: bool):
        """启用/禁用 airPLS 基线校正"""
        for dev in self.device_manager.devices.values():
            dev.baseline_enabled = enabled
        self.chk_show_bl.setEnabled(enabled)
        if not enabled:
            self.chk_show_bl.setChecked(False)
        self._refresh_all_curves()

    def _on_baseline_param_changed(self):
        """更新基线校正参数 (lambda, order)"""
        lam_str = self.combo_bl_lam.currentText()
        lam = float(lam_str)
        order = self.spin_bl_order.value()
        for dev in self.device_manager.devices.values():
            dev.baseline_lam = lam
            dev.baseline_order = order
        self._refresh_all_curves()

    def _on_show_baseline_toggled(self, show: bool):
        """切换基线叠加显示"""
        if not show:
            self.plot_widget.clear_reference_curves()
        self._refresh_all_curves()

    def _refresh_all_curves(self):
        """用户触发刷新(模式/轴切换) — 直接绘图, 不走节流"""
        for device_id, device in self.device_manager.devices.items():
            if device.latest_pixels is not None:
                x = self._get_x_axis_data(device)
                mode_map = {'原始光谱': 'raw', '扣背景光谱': 'dark_subtract',
                            '吸收光谱': 'absorbance', '自定义公式': 'custom'}
                mode_str = mode_map.get(self.combo_display_mode.currentText(), 'raw')
                result = device.get_display_spectrum(mode_str, self.custom_formula)
                if result is not None:
                    _, y = result
                    sn = device.info.prod_serial or f'SN{device.info.serial_num}' if device.info.serial_num else f'Ch{device_id}'
                    self.plot_widget.update_device_curve(device_id, x, y, sn)
        self._update_baseline_curves()
        if self.chk_auto_range.isChecked():
            self.plot_widget.plot.enableAutoRange()

    def _update_baseline_curves(self):
        """更新基线叠加显示曲线"""
        self.plot_widget.clear_reference_curves()
        if not self.chk_show_bl.isChecked():
            return
        for device_id, device in self.device_manager.devices.items():
            if device.baseline_enabled and device.baseline_y is not None:
                x = self._get_x_axis_data(device)
                # 基线颜色 = 设备颜色, 虚线
                color = COLOR_PALETTE[device_id % len(COLOR_PALETTE)]
                sn = device.info.prod_serial or f'Ch{device_id}'
                self.plot_widget.add_reference_curve(x, device.baseline_y,
                                                     f'基线-{sn}', color)

    def _on_calib_ready(self, device_id: int, c1, c2, c3, c4):
        self._refresh_all_curves()

    def _on_device_error(self, device_id: int, error_msg: str):
        self.status_bar.showMessage(f'[设备{device_id}] {error_msg}', 5000)

    # ==================== 存储 ====================

    def _save_current_spectrum(self):
        frames_by_device = {}
        for did, wl, px, ts in self.acq_engine.buffer:
            if did not in frames_by_device:
                frames_by_device[did] = ([], [])
            frames_by_device[did][0].append(wl)
            frames_by_device[did][1].append(px)

        if not frames_by_device:
            for did, dev in self.device_manager.devices.items():
                if dev.latest_pixels is not None and dev.latest_wavelengths is not None:
                    frames_by_device[did] = ([dev.latest_wavelengths], [dev.latest_pixels])

        if not frames_by_device:
            QMessageBox.information(self, '提示', '无数据可保存')
            return

        saved = self.data_exporter.export_buffered(
            self.device_manager, frames_by_device, self.settings.get('storage_path', './data'))
        self.status_bar.showMessage(f'已保存 {saved} 个文件', 3000)

    def _save_all_channels(self):
        self._save_current_spectrum()

    def _export_background(self):
        for d in self.device_manager.devices.values():
            if d.background_spectrum is not None:
                self.data_exporter.export_background(d)

    def _export_reference(self):
        for d in self.device_manager.devices.values():
            if d.reference_spectrum is not None:
                self.data_exporter.export_reference(d)

    def _save_plot_image(self):
        path, _ = QFileDialog.getSaveFileName(
            self, '保存光谱图片', '', 'PNG (*.png);;JPEG (*.jpg);;BMP (*.bmp)')
        if not path:
            return
        pixmap = self.plot_widget.grab()
        pixmap.save(path)
        self.status_bar.showMessage(f'图片已保存: {path}', 3000)

    # ==================== 菜单 ====================

    def _open_settings(self):
        dialog = SettingsDialog(self, self.settings)
        dialog.settings_changed.connect(self._on_settings_changed)
        if dialog.exec_():
            self._on_settings_changed(dialog.get_settings())

    def _on_settings_changed(self, settings: dict):
        self.settings.update(settings)
        self.data_exporter.set_default_path(settings.get('storage_path', './data'))
        self.acq_engine.auto_save_enabled = settings.get('auto_save', False)
        self.acq_engine.auto_save_mode = settings.get('save_mode', 'immediate')
        self.acq_engine.auto_save_count = settings.get('save_param', 10)
        self.acq_engine.auto_save_interval_s = float(settings.get('save_param', 5))
        self.plot_widget.set_line_width(settings.get('line_width', 1.0))

    def _open_history_viewer(self):
        if self.history_viewer is None:
            self.history_viewer = HistoryViewer()
        self.history_viewer.show()
        self.history_viewer.raise_()

    def _show_about(self):
        QMessageBox.about(self, '关于 ZGCAI 光谱仪控制软件',
                          '<h3>ZGCAI 光谱仪控制软件 v1.0</h3>'
                          '<p>基于 Python + PyQt5 开发</p>'
                          '<p>设备卡片式管理，独立控制每台光谱仪</p>'
                          '<hr>'
                          '<p>通信协议: 二进制包协议 (0x24帧头)</p>')

    # ==================== 生命周期 ====================

    def closeEvent(self, event):
        if self.acq_engine.is_running():
            self.acq_engine.stop()
        for did in list(self.device_manager.devices.keys()):
            self.device_manager.remove_device(did)
        event.accept()
