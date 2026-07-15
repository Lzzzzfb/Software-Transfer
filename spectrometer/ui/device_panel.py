"""
设备卡片面板 — 每个光谱仪设备独立一张卡片，自带完整控制组件。
按钮样式通过动态属性 btnRole 由全局 QSS 控制，保持 hover 效果一致。
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QSpinBox, QDoubleSpinBox, QLineEdit, QMenu,
    QGridLayout, QFrame, QScrollArea, QSizePolicy, QMessageBox,
    QFileDialog, QAction
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer, QSize

from ..device.spectrometer import SpectrometerDevice
from ..device.device_manager import DeviceManager


# 卡片样式 — QGroupBox 需要选择器语法
ENABLED_CARD = "QGroupBox { background: #fff; border: 1px solid #e0e0e0; border-left: 4px solid #34a853; border-radius: 8px; margin-top: 6px; padding-top: 20px; }"
DISABLED_CARD = "QGroupBox { background: #f5f5f5; border: 1px solid #e0e0e0; border-left: 4px solid #ea4335; border-radius: 8px; margin-top: 6px; padding-top: 20px; }"


def _apply_role(btn: QPushButton, role: str):
    """设置按钮角色属性, 匹配全局 QSS 中的 QPushButton[btnRole=\"...\"] 选择器"""
    btn.setProperty("btnRole", role)
    btn.style().unpolish(btn)
    btn.style().polish(btn)


class DeviceCard(QGroupBox):
    """单个设备的控制卡片 — 5行布局"""

    bg_requested = pyqtSignal(int)
    ref_requested = pyqtSignal(int)
    acq_start_requested = pyqtSignal(int, bool)
    acq_stop_requested = pyqtSignal(int)
    integ_time_requested = pyqtSignal(int, int)
    avg_count_requested = pyqtSignal(int, int)
    enable_toggled = pyqtSignal(int, bool)
    more_params_requested = pyqtSignal(int)
    close_requested = pyqtSignal(int)

    def __init__(self, device_id: int, device: SpectrometerDevice, parent=None):
        super().__init__(parent)
        self.device_id = device_id
        self.device = device
        self._enabled = True
        self._acquiring = False
        self._acq_mode = True

        self.setMinimumWidth(280)
        self.setMaximumWidth(360)
        self._setup_ui()
        self._update_style()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(8, 16, 8, 8)

        # ---- Row 1: 设备名称 + 启用/停用/关闭 ----
        row1 = QHBoxLayout()
        row1.setSpacing(6)
        self.lbl_name = QLabel('')
        self.lbl_name.setStyleSheet('font-weight: bold; font-size: 13px; color: #333;')
        row1.addWidget(self.lbl_name, stretch=1)

        self.btn_enable = QPushButton('启用')
        _apply_role(self.btn_enable, "enable")
        self.btn_enable.clicked.connect(self._on_enable)
        row1.addWidget(self.btn_enable)

        self.btn_disable = QPushButton('停用')
        _apply_role(self.btn_disable, "disable")
        self.btn_disable.clicked.connect(self._on_disable)
        self.btn_disable.setVisible(False)
        row1.addWidget(self.btn_disable)

        self.btn_close = QPushButton('✕')
        self.btn_close.setToolTip('断开并移除此设备')
        self.btn_close.setFixedSize(26, 26)
        _apply_role(self.btn_close, "close")
        self.btn_close.clicked.connect(lambda: self.close_requested.emit(self.device_id))
        row1.addWidget(self.btn_close)

        layout.addLayout(row1)

        # ---- Row 2: 背景 / 参考 / 开始-停止 ----
        row2 = QHBoxLayout()
        row2.setSpacing(6)

        self.btn_bg = QPushButton('背景')
        self.btn_bg.setToolTip('将当前光谱保存为背景光谱')
        self.btn_bg.clicked.connect(lambda: self.bg_requested.emit(self.device_id))
        row2.addWidget(self.btn_bg)

        self.btn_ref = QPushButton('参考')
        self.btn_ref.setToolTip('将当前光谱保存为参考光谱')
        self.btn_ref.clicked.connect(lambda: self.ref_requested.emit(self.device_id))
        row2.addWidget(self.btn_ref)

        self.btn_acq = QPushButton('▶ 开始采集')
        _apply_role(self.btn_acq, "start")
        self.btn_acq.clicked.connect(self._on_acq_clicked)
        self.btn_acq.setContextMenuPolicy(Qt.CustomContextMenu)
        self.btn_acq.customContextMenuRequested.connect(self._on_acq_context_menu)
        row2.addWidget(self.btn_acq, stretch=1)

        layout.addLayout(row2)

        # ---- Row 3: 曝光时间 ----
        row3 = QHBoxLayout()
        row3.setSpacing(6)
        row3.addWidget(QLabel('积分(us):'))
        self.edit_integ = QSpinBox()
        self.edit_integ.setRange(1, 99999999)
        self.edit_integ.setValue(10000)
        self.edit_integ.setSingleStep(1000)
        self.edit_integ.setMaximumWidth(120)
        row3.addWidget(self.edit_integ)
        self.btn_set_integ = QPushButton('设置')
        self.btn_set_integ.clicked.connect(
            lambda: self.integ_time_requested.emit(self.device_id, self.edit_integ.value()))
        row3.addWidget(self.btn_set_integ)
        row3.addStretch()
        layout.addLayout(row3)

        # ---- Row 4: 平均次数 ----
        row4 = QHBoxLayout()
        row4.setSpacing(6)
        row4.addWidget(QLabel('平均次数:'))
        self.edit_avg = QSpinBox()
        self.edit_avg.setRange(1, 10000)
        self.edit_avg.setValue(1)
        self.edit_avg.setMaximumWidth(80)
        row4.addWidget(self.edit_avg)
        self.btn_set_avg = QPushButton('设置')
        self.btn_set_avg.clicked.connect(
            lambda: self.avg_count_requested.emit(self.device_id, self.edit_avg.value()))
        row4.addWidget(self.btn_set_avg)
        row4.addStretch()
        layout.addLayout(row4)

        # ---- Row 5: 更多参数 ----
        row5 = QHBoxLayout()
        self.btn_more = QPushButton('更多参数...')
        self.btn_more.clicked.connect(lambda: self.more_params_requested.emit(self.device_id))
        row5.addWidget(self.btn_more)
        row5.addStretch()
        layout.addLayout(row5)

    # ==================== 公共接口 ====================

    def update_from_device(self, device: SpectrometerDevice):
        self.device = device
        sn = device.info.prod_serial or f"SN:{device.info.serial_num}"
        title = f"{device.port_name}  [{sn}]"
        if device.connected:
            title += "  ●"
        self.lbl_name.setText(title)
        self.setTitle('')

        if not self._acquiring:
            self.edit_integ.blockSignals(True)
            self.edit_integ.setValue(device.integration_time_us)
            self.edit_integ.blockSignals(False)
            self.edit_avg.blockSignals(True)
            self.edit_avg.setValue(device.avg_count)
            self.edit_avg.blockSignals(False)

    def set_enabled_state(self, enabled: bool):
        self._enabled = enabled
        self._update_style()

    def set_acquiring(self, acquiring: bool):
        self._acquiring = acquiring
        if acquiring:
            self.btn_acq.setText('■ 停止')
            _apply_role(self.btn_acq, "stop")
        else:
            mode = '连续' if self._acq_mode else '单次'
            self.btn_acq.setText('▶ 开始采集')
            self.btn_acq.setToolTip(f'当前模式: {mode}采集\n右击切换单次/连续')
            _apply_role(self.btn_acq, "start")

    def is_enabled(self) -> bool:
        return self._enabled

    # ==================== 内部槽 ====================

    def _on_enable(self):
        self._enabled = True
        self._update_style()
        self.enable_toggled.emit(self.device_id, True)

    def _on_disable(self):
        self._enabled = False
        self._update_style()
        self.enable_toggled.emit(self.device_id, False)

    def _update_style(self):
        if self._enabled:
            self.setStyleSheet(ENABLED_CARD)
        else:
            self.setStyleSheet(DISABLED_CARD)
        self.btn_enable.setVisible(not self._enabled)
        self.btn_disable.setVisible(self._enabled)

    def _on_acq_clicked(self):
        if self._acquiring:
            self.acq_stop_requested.emit(self.device_id)
        else:
            self.acq_start_requested.emit(self.device_id, self._acq_mode)

    def _on_acq_context_menu(self, pos):
        menu = QMenu(self)
        act_cont = QAction('连续采集', menu)
        act_cont.setCheckable(True)
        act_cont.setChecked(self._acq_mode)
        act_single = QAction('单次采集', menu)
        act_single.setCheckable(True)
        act_single.setChecked(not self._acq_mode)
        menu.addAction(act_cont)
        menu.addAction(act_single)
        chosen = menu.exec_(self.btn_acq.mapToGlobal(pos))
        if chosen is act_cont:
            self._acq_mode = True
        elif chosen is act_single:
            self._acq_mode = False
        mode = '连续' if self._acq_mode else '单次'
        self.btn_acq.setToolTip(f'当前模式: {mode}采集\n右击切换单次/连续')


class DeviceCardPanel(QWidget):
    """设备卡片面板 — 顶部总控按钮 + 滚动区域容纳所有 DeviceCard"""

    bg_requested = pyqtSignal(int)
    ref_requested = pyqtSignal(int)
    acq_start_requested = pyqtSignal(int, bool)
    acq_stop_requested = pyqtSignal(int)
    integ_time_requested = pyqtSignal(int, int)
    avg_count_requested = pyqtSignal(int, int)
    enable_toggled = pyqtSignal(int, bool)
    more_params_requested = pyqtSignal(int)
    close_requested = pyqtSignal(int)

    master_start_requested = pyqtSignal(bool)
    master_stop_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(300)
        self.setMaximumWidth(390)
        self.cards = {}
        self._master_acq_mode = True
        self._master_acquiring = False

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # ---- 总控按钮 ----
        self.master_btn = QPushButton('▶ 全部开始采集')
        _apply_role(self.master_btn, "masterStart")
        self.master_btn.clicked.connect(self._on_master_clicked)
        self.master_btn.setContextMenuPolicy(Qt.CustomContextMenu)
        self.master_btn.customContextMenuRequested.connect(self._on_master_context_menu)
        self.master_btn.setToolTip('总控: 同时操控所有设备\n右击切换单次/连续采集')
        main_layout.addWidget(self.master_btn)

        # 滚动区域
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)

        self._card_container = QWidget()
        self._card_layout = QVBoxLayout(self._card_container)
        self._card_layout.setSpacing(6)
        self._card_layout.setContentsMargins(4, 4, 4, 4)
        self._card_layout.addStretch()

        self.scroll.setWidget(self._card_container)
        main_layout.addWidget(self.scroll, stretch=1)

    def set_master_acquiring(self, acquiring: bool):
        self._master_acquiring = acquiring
        if acquiring:
            self.master_btn.setText('■ 全部停止')
            _apply_role(self.master_btn, "masterStop")
        else:
            self.master_btn.setText('▶ 全部开始采集')
            _apply_role(self.master_btn, "masterStart")
            mode = '连续' if self._master_acq_mode else '单次'
            self.master_btn.setToolTip(f'总控: 同时操控所有设备\n当前模式: {mode}采集\n右击切换单次/连续')

    def _on_master_clicked(self):
        if self._master_acquiring:
            self.master_stop_requested.emit()
        else:
            self.master_start_requested.emit(self._master_acq_mode)

    def _on_master_context_menu(self, pos):
        menu = QMenu(self)
        act_cont = QAction('连续采集', menu, checkable=True, checked=self._master_acq_mode)
        act_single = QAction('单次采集', menu, checkable=True, checked=not self._master_acq_mode)
        menu.addAction(act_cont)
        menu.addAction(act_single)
        chosen = menu.exec_(self.master_btn.mapToGlobal(pos))
        if chosen is act_cont:
            self._master_acq_mode = True
        elif chosen is act_single:
            self._master_acq_mode = False
        mode = '连续' if self._master_acq_mode else '单次'
        self.master_btn.setToolTip(f'总控: 同时操控所有设备\n当前模式: {mode}采集\n右击切换单次/连续')

    def add_card(self, device_id: int, device: SpectrometerDevice) -> DeviceCard:
        card = DeviceCard(device_id, device)
        card.update_from_device(device)
        card.bg_requested.connect(self.bg_requested)
        card.ref_requested.connect(self.ref_requested)
        card.acq_start_requested.connect(self.acq_start_requested)
        card.acq_stop_requested.connect(self.acq_stop_requested)
        card.integ_time_requested.connect(self.integ_time_requested)
        card.avg_count_requested.connect(self.avg_count_requested)
        card.enable_toggled.connect(self.enable_toggled)
        card.more_params_requested.connect(self.more_params_requested)
        card.close_requested.connect(self.close_requested)
        self._card_layout.insertWidget(self._card_layout.count() - 1, card)
        self.cards[device_id] = card
        return card

    def remove_card(self, device_id: int):
        card = self.cards.pop(device_id, None)
        if card:
            self._card_layout.removeWidget(card)
            card.deleteLater()

    def update_card(self, device_id: int, device: SpectrometerDevice):
        card = self.cards.get(device_id)
        if card:
            card.update_from_device(device)

    def set_card_acquiring(self, device_id: int, acquiring: bool):
        card = self.cards.get(device_id)
        if card:
            card.set_acquiring(acquiring)

    def get_card(self, device_id: int) -> DeviceCard | None:
        return self.cards.get(device_id)

    def clear_all(self):
        for did in list(self.cards.keys()):
            self.remove_card(did)
