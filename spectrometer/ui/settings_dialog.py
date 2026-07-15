"""
设置对话框 — 存储路径、自动存储、可视化参数配置。
"""

import os
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox,
    QCheckBox, QFileDialog, QTabWidget, QWidget, QFormLayout,
    QDialogButtonBox, QColorDialog
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor


class SettingsDialog(QDialog):
    """全局设置对话框"""

    settings_changed = pyqtSignal(dict)

    def __init__(self, parent=None, current_settings: dict = None):
        super().__init__(parent)
        self.setWindowTitle('设置')
        self.setMinimumSize(500, 400)
        self.settings = current_settings or {}
        self._setup_ui()

        if current_settings:
            self._load_settings(current_settings)

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        tabs = QTabWidget()

        # ---- 存储设置 ----
        storage_tab = QWidget()
        storage_layout = QVBoxLayout(storage_tab)

        # 存储路径
        path_group = QGroupBox('存储路径')
        path_layout = QHBoxLayout(path_group)
        self.edit_storage_path = QLineEdit()
        self.edit_storage_path.setText(os.path.abspath('./data'))
        path_layout.addWidget(self.edit_storage_path)
        self.btn_browse_path = QPushButton('浏览...')
        self.btn_browse_path.clicked.connect(self._browse_path)
        path_layout.addWidget(self.btn_browse_path)
        storage_layout.addWidget(path_group)

        # 自动存储
        auto_group = QGroupBox('自动存储')
        auto_layout = QVBoxLayout(auto_group)
        self.chk_auto_save = QCheckBox('启用自动存储')
        auto_layout.addWidget(self.chk_auto_save)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel('存储模式:'))
        self.combo_save_mode = QComboBox()
        self.combo_save_mode.addItems(['立即存储', '按数据量间隔', '按时间间隔'])
        mode_row.addWidget(self.combo_save_mode)
        auto_layout.addLayout(mode_row)

        param_row = QHBoxLayout()
        param_row.addWidget(QLabel('间隔参数:'))
        self.spin_save_param = QSpinBox()
        self.spin_save_param.setRange(1, 9999)
        self.spin_save_param.setValue(10)
        param_row.addWidget(self.spin_save_param)
        self.lbl_save_param_unit = QLabel('帧')
        param_row.addWidget(self.lbl_save_param_unit)
        auto_layout.addLayout(param_row)

        self.combo_save_mode.currentIndexChanged.connect(self._on_save_mode_changed)

        # 文件格式
        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel('文件格式:'))
        self.combo_format = QComboBox()
        self.combo_format.addItems(['CSV', 'TXT'])
        fmt_row.addWidget(self.combo_format)
        auto_layout.addLayout(fmt_row)

        self.chk_merge_files = QCheckBox('多设备数据合并为一个文件')
        auto_layout.addWidget(self.chk_merge_files)

        # 文件命名
        name_group = QGroupBox('文件命名包含信息')
        name_layout = QVBoxLayout(name_group)
        self.chk_name_time = QCheckBox('采集时间')
        self.chk_name_time.setChecked(True)
        name_layout.addWidget(self.chk_name_time)
        self.chk_name_exp = QCheckBox('曝光时间')
        self.chk_name_exp.setChecked(True)
        name_layout.addWidget(self.chk_name_exp)
        self.chk_name_trig = QCheckBox('外触发标识(int/ext)')
        self.chk_name_trig.setChecked(True)
        name_layout.addWidget(self.chk_name_trig)
        self.chk_name_delay = QCheckBox('延迟时间')
        name_layout.addWidget(self.chk_name_delay)
        auto_layout.addWidget(name_group)

        storage_layout.addWidget(auto_group)
        storage_layout.addStretch()
        tabs.addTab(storage_tab, '存储')

        # ---- 可视化设置 ----
        vis_tab = QWidget()
        vis_layout = QVBoxLayout(vis_tab)

        curve_group = QGroupBox('曲线样式')
        curve_layout = QFormLayout(curve_group)
        self.spin_line_width = QDoubleSpinBox()
        self.spin_line_width.setRange(0.5, 10.0)
        self.spin_line_width.setValue(1.0)
        self.spin_line_width.setSingleStep(0.5)
        curve_layout.addRow('默认线宽:', self.spin_line_width)

        self.chk_auto_range = QCheckBox('采集时自动缩放坐标轴')
        self.chk_auto_range.setChecked(True)
        curve_layout.addRow(self.chk_auto_range)

        vis_layout.addWidget(curve_group)

        # 颜色设置
        color_group = QGroupBox('默认曲线颜色')
        color_layout = QVBoxLayout(color_group)
        self.chk_use_default_colors = QCheckBox('使用内置颜色循环')
        self.chk_use_default_colors.setChecked(True)
        color_layout.addWidget(self.chk_use_default_colors)

        vis_layout.addWidget(color_group)
        vis_layout.addStretch()
        tabs.addTab(vis_tab, '可视化')

        layout.addWidget(tabs)

        # 确定/取消按钮
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_save_mode_changed(self, idx: int):
        if idx == 0:  # 立即存储
            self.spin_save_param.setEnabled(False)
        elif idx == 1:  # 按数据量
            self.spin_save_param.setEnabled(True)
            self.lbl_save_param_unit.setText('帧')
        else:  # 按时间
            self.spin_save_param.setEnabled(True)
            self.lbl_save_param_unit.setText('秒')

    def _browse_path(self):
        path = QFileDialog.getExistingDirectory(self, '选择存储路径')
        if path:
            self.edit_storage_path.setText(path)

    def _load_settings(self, settings: dict):
        if 'storage_path' in settings:
            self.edit_storage_path.setText(settings['storage_path'])
        if 'auto_save' in settings:
            self.chk_auto_save.setChecked(settings['auto_save'])
        if 'save_mode' in settings:
            self.combo_save_mode.setCurrentIndex(
                {'immediate': 0, 'count': 1, 'time': 2}.get(settings['save_mode'], 0))
        if 'save_param' in settings:
            self.spin_save_param.setValue(settings['save_param'])
        if 'merge_files' in settings:
            self.chk_merge_files.setChecked(settings['merge_files'])
        if 'line_width' in settings:
            self.spin_line_width.setValue(settings['line_width'])
        if 'auto_range' in settings:
            self.chk_auto_range.setChecked(settings['auto_range'])

    def _on_accept(self):
        self.settings = {
            'storage_path': self.edit_storage_path.text(),
            'auto_save': self.chk_auto_save.isChecked(),
            'save_mode': ['immediate', 'count', 'time'][self.combo_save_mode.currentIndex()],
            'save_param': self.spin_save_param.value(),
            'merge_files': self.chk_merge_files.isChecked(),
            'line_width': self.spin_line_width.value(),
            'auto_range': self.chk_auto_range.isChecked(),
        }
        self.settings_changed.emit(self.settings)
        self.accept()

    def get_settings(self) -> dict:
        return self.settings


class HistoryViewer(QWidget):
    """历史数据查看器 — 支持多标签页，每个标签最多64条曲线"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('历史数据查看器')
        self.resize(900, 600)
        self._setup_ui()
        self._tab_count = 0

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # 工具栏
        toolbar = QHBoxLayout()
        self.btn_add_tab = QPushButton('新建标签页')
        self.btn_add_tab.clicked.connect(self._on_add_tab)
        toolbar.addWidget(self.btn_add_tab)

        self.btn_load_file = QPushButton('加载文件...')
        self.btn_load_file.clicked.connect(self._on_load_file)
        toolbar.addWidget(self.btn_load_file)

        self.btn_close_tab = QPushButton('关闭当前标签')
        self.btn_close_tab.clicked.connect(self._on_close_tab)
        toolbar.addWidget(self.btn_close_tab)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # 标签页
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self._on_tab_close)
        layout.addWidget(self.tab_widget)

        # 添加一个默认标签
        self._add_tab('历史数据 1')

    def _add_tab(self, name: str):
        from .plot_widget import HistoryPlotWidget
        widget = HistoryPlotWidget()
        idx = self.tab_widget.addTab(widget, name)
        self.tab_widget.setCurrentIndex(idx)
        return idx

    def _on_add_tab(self):
        if self.tab_widget.count() >= 12:
            return
        self._tab_count += 1
        self._add_tab(f'历史数据 {self.tab_widget.count() + 1}')

    def _on_close_tab(self):
        idx = self.tab_widget.currentIndex()
        if idx >= 0:
            self.tab_widget.removeTab(idx)

    def _on_tab_close(self, idx: int):
        if self.tab_widget.count() > 1:
            self.tab_widget.removeTab(idx)

    def _on_load_file(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, '加载光谱数据', '', 'CSV/TXT Files (*.csv *.txt);;All Files (*)')
        if not paths:
            return
        widget = self.tab_widget.currentWidget()
        if widget is None:
            self._add_tab('历史数据')
            widget = self.tab_widget.currentWidget()

        import numpy as np
        import csv
        import os

        for path in paths:
            try:
                wavelengths, intensities = [], []
                with open(path, 'r', encoding='utf-8') as f:
                    reader = csv.reader(f)
                    for row in reader:
                        if not row or row[0].startswith('#'):
                            continue
                        if row[0] in ('Wavelength', '波长'):
                            continue
                        try:
                            wavelengths.append(float(row[0]))
                            intensities.append(float(row[1]))
                        except (ValueError, IndexError):
                            continue

                if wavelengths and hasattr(widget, 'load_spectrum'):
                    label = os.path.basename(path)
                    widget.load_spectrum(
                        np.array(wavelengths), np.array(intensities), label=label)
            except Exception as e:
                pass  # 静默跳过无法解析的文件

    def load_data(self, wavelengths, intensities, label: str):
        """直接从内存数据加载"""
        widget = self.tab_widget.currentWidget()
        if widget and hasattr(widget, 'load_spectrum'):
            widget.load_spectrum(wavelengths, intensities, label=label)


class DeviceParamsDialog(QDialog):
    """设备更多参数弹窗 — 显示版本信息、校准系数等"""

    def __init__(self, parent, device, query_signal, param_signal, device_id):
        super().__init__(parent)
        self.device = device
        self.device_id = device_id
        self.query_signal = query_signal
        self.param_signal = param_signal
        self.setWindowTitle(f'设备参数 — {device.port_name}')
        self.setMinimumWidth(480)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        d = self.device

        # ---- 版本信息 ----
        info_group = QGroupBox('设备信息')
        info_layout = QFormLayout(info_group)
        info_layout.addRow('端口:', QLabel(d.port_name))
        info_layout.addRow('设备名:', QLabel(str(d.info.name)))
        info_layout.addRow('设备类型:', QLabel(str(d.info.dev_type)))
        info_layout.addRow('硬件版本:', QLabel(f'{d.info.hw_ver:.2f}'))
        info_layout.addRow('固件版本:', QLabel(f'{d.info.fw_ver:.2f}'))
        info_layout.addRow('序列号:', QLabel(str(d.info.serial_num)))
        info_layout.addRow('总像素:', QLabel(str(d.info.pixel_count)))
        info_layout.addRow('起始像素:', QLabel(str(d.info.start_pixel)))
        info_layout.addRow('有效像素:', QLabel(str(d.info.valid_pixel)))
        info_layout.addRow('曝光最小(us):', QLabel(str(d.info.pos_time_min)))
        info_layout.addRow('曝光最大(us):', QLabel(str(d.info.pos_time_max)))
        btn_query = QPushButton('刷新设备信息')
        btn_query.clicked.connect(lambda: self.query_signal.emit(self.device_id, 0x01))
        info_layout.addRow(btn_query)
        btn_query_sn = QPushButton('查询产品序列号')
        btn_query_sn.clicked.connect(lambda: self.query_signal.emit(self.device_id, 0x3c))
        info_layout.addRow(btn_query_sn)
        layout.addWidget(info_group)

        # ---- 波长校准系数 ----
        calib_group = QGroupBox('波长校准系数 (C1+C2*x+C3*x²+C4*x³)')
        calib_layout = QFormLayout(calib_group)
        _s = 'QDoubleSpinBox { min-width: 240px; }'

        self.spin_c1 = QDoubleSpinBox()
        self.spin_c1.setRange(-1e9, 1e9)
        self.spin_c1.setDecimals(20)
        self.spin_c1.setValue(d.wavelength_calib.c1)
        self.spin_c1.setStyleSheet(_s)
        self.spin_c1.valueChanged.connect(self._on_calib_changed)
        calib_layout.addRow('C1 (0阶):', self.spin_c1)

        self.spin_c2 = QDoubleSpinBox()
        self.spin_c2.setRange(-1e9, 1e9)
        self.spin_c2.setDecimals(20)
        self.spin_c2.setValue(d.wavelength_calib.c2)
        self.spin_c2.setStyleSheet(_s)
        self.spin_c2.valueChanged.connect(self._on_calib_changed)
        calib_layout.addRow('C2 (1阶):', self.spin_c2)

        self.spin_c3 = QDoubleSpinBox()
        self.spin_c3.setRange(-1e9, 1e9)
        self.spin_c3.setDecimals(20)
        self.spin_c3.setValue(d.wavelength_calib.c3)
        self.spin_c3.setStyleSheet(_s)
        self.spin_c3.valueChanged.connect(self._on_calib_changed)
        calib_layout.addRow('C3 (2阶):', self.spin_c3)

        self.spin_c4 = QDoubleSpinBox()
        self.spin_c4.setRange(-1e9, 1e9)
        self.spin_c4.setDecimals(20)
        self.spin_c4.setValue(d.wavelength_calib.c4)
        self.spin_c4.setStyleSheet(_s)
        self.spin_c4.valueChanged.connect(self._on_calib_changed)
        calib_layout.addRow('C4 (3阶):', self.spin_c4)

        btn_row = QHBoxLayout()
        btn_query_calib = QPushButton('从设备查询')
        btn_query_calib.clicked.connect(lambda: self.query_signal.emit(self.device_id, 0x37))
        btn_row.addWidget(btn_query_calib)
        btn_row.addStretch()
        calib_layout.addRow(btn_row)
        layout.addWidget(calib_group)

        # ---- 其他参数 ----
        other_group = QGroupBox('当前采集参数')
        other_layout = QFormLayout(other_group)
        other_layout.addRow('积分时间(us):', QLabel(str(d.integration_time_us)))
        other_layout.addRow('触发模式:', QLabel(['软件触发', '软触发主机', '外部触发'][d.trigger_mode]))
        other_layout.addRow('平均次数:', QLabel(str(d.avg_count)))
        other_layout.addRow('增益:', QLabel(str(d.gain)))
        other_layout.addRow('采集间隔(us):', QLabel(str(d.interval_us)))
        other_layout.addRow('触发延时(us):', QLabel(str(d.delay_us)))
        btn_query2 = QPushButton('刷新采集参数')
        btn_query2.clicked.connect(lambda: [
            self.query_signal.emit(self.device_id, c) for c in
            [0x30, 0x31, 0x33, 0x35, 0x3b]])
        other_layout.addRow(btn_query2)
        layout.addWidget(other_group)

        # ---- 关闭 ----
        btn_close = QPushButton('关闭')
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)

    def _on_calib_changed(self):
        """校准系数变更 → 实时同步到设备模型"""
        c1 = self.spin_c1.value()
        c2 = self.spin_c2.value()
        c3 = self.spin_c3.value()
        c4 = self.spin_c4.value()
        self.param_signal.emit(self.device_id, 'calib_coeff', (c1, c2, c3, c4))

    def update_calib(self, c1: float, c2: float, c3: float, c4: float):
        """外部调用：更新系数显示（设备查询返回时）"""
        for spin, val in [(self.spin_c1, c1), (self.spin_c2, c2),
                          (self.spin_c3, c3), (self.spin_c4, c4)]:
            spin.blockSignals(True)
            spin.setValue(val)
            spin.blockSignals(False)
