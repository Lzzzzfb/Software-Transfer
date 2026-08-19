"""左侧总控和设备卡片。"""

from ..qt import QtCore, QtWidgets, Signal
from .input_controls import DirectSpinBox


class DeviceCard(QtWidgets.QFrame):
    selected = Signal(int)
    enabled_changed = Signal(int, bool)
    integration_changed = Signal(int, int)
    parameters_requested = Signal(int)
    disconnect_requested = Signal(int)
    acquisition_requested = Signal(int)
    background_requested = Signal(int)
    reference_requested = Signal(int)

    def __init__(self, device, parent=None):
        super().__init__(parent)
        self.device_id = device.device_id
        self.setObjectName("deviceCard")
        self.setProperty("selected", False)
        layout = QtWidgets.QVBoxLayout(self); layout.setContentsMargins(10, 8, 10, 8)
        top = QtWidgets.QHBoxLayout()
        self.radio = QtWidgets.QRadioButton()
        self.radio.clicked.connect(lambda: self.selected.emit(self.device_id))
        top.addWidget(self.radio)
        self.title = QtWidgets.QLabel(); self.title.setObjectName("deviceTitle"); top.addWidget(self.title, 1)
        self.enabled = QtWidgets.QCheckBox("参与总控"); self.enabled.setChecked(True)
        self.enabled.setToolTip("仅决定顶部总控是否包含本设备，不影响本卡片单独采集")
        self.enabled.toggled.connect(lambda value: self.enabled_changed.emit(self.device_id, value))
        top.addWidget(self.enabled)
        disconnect = QtWidgets.QToolButton(); disconnect.setText("×"); disconnect.setToolTip("断开并移除设备")
        disconnect.clicked.connect(lambda: self.disconnect_requested.emit(self.device_id)); top.addWidget(disconnect)
        layout.addLayout(top)
        self.details = QtWidgets.QLabel(); self.details.setObjectName("deviceDetails"); layout.addWidget(self.details)
        controls = QtWidgets.QHBoxLayout(); controls.addWidget(QtWidgets.QLabel("积分 µs"))
        self.integration = DirectSpinBox(); self.integration.setRange(1, 99_999_999)
        self.integration.setSingleStep(1000); self.integration.setValue(device.integration_time_us)
        controls.addWidget(self.integration, 1)
        apply_button = QtWidgets.QPushButton("应用")
        apply_button.clicked.connect(lambda: self.integration_changed.emit(self.device_id, self.integration.value()))
        controls.addWidget(apply_button); layout.addLayout(controls)
        parameters = QtWidgets.QPushButton("设备参数与校准…")
        parameters.clicked.connect(lambda: self.parameters_requested.emit(self.device_id))
        layout.addWidget(parameters)
        acquisition_controls = QtWidgets.QHBoxLayout()
        self.acquisition_button = QtWidgets.QPushButton("开始")
        self.acquisition_button.setObjectName("deviceAcquisitionButton")
        self.acquisition_button.clicked.connect(
            lambda: self.acquisition_requested.emit(self.device_id)
        )
        self.background_button = QtWidgets.QPushButton("背景")
        self.background_button.clicked.connect(
            lambda: self.background_requested.emit(self.device_id)
        )
        self.reference_button = QtWidgets.QPushButton("参考")
        self.reference_button.clicked.connect(
            lambda: self.reference_requested.emit(self.device_id)
        )
        acquisition_controls.addWidget(self.acquisition_button, 2)
        acquisition_controls.addWidget(self.background_button, 1)
        acquisition_controls.addWidget(self.reference_button, 1)
        layout.addLayout(acquisition_controls)
        self.update_device(device)

    def update_device(self, device):
        serial = device.info.prod_serial or str(device.info.serial_num or "未识别")
        state = "已连接" if device.connected else "重连中"
        self.title.setText(f"{device.port_name}  ·  {state}")
        self.details.setText(
            f"SN {serial}   |   {device.info.valid_pixel or device.info.pixel_count or '--'} px\n"
            f"{device.start_wavelength:.2f} nm 起始   |   增益 {device.gain}"
        )
        self.integration.setValue(device.integration_time_us)

    def set_selected(self, selected: bool):
        self.radio.setChecked(selected); self.setProperty("selected", selected)
        self.style().unpolish(self); self.style().polish(self)

    def set_acquisition_state(self, state: str):
        labels = {
            "idle": "开始",
            "configuring": "停止",
            "starting": "停止",
            "armed": "停止",
            "acquiring": "停止",
            "stopping": "停止中…",
            "finalizing": "保存中…",
            "error": "开始",
        }
        self.acquisition_button.setText(labels.get(str(state), "开始"))

    def set_scan_locked(self, locked: bool):
        enabled = not bool(locked)
        self.acquisition_button.setEnabled(enabled)
        self.background_button.setEnabled(enabled)
        self.reference_button.setEnabled(enabled)


class DeviceSidebar(QtWidgets.QWidget):
    selection_changed = Signal(int)
    integration_changed = Signal(int, int)
    enabled_changed = Signal(int, bool)
    parameters_requested = Signal(int)
    disconnect_requested = Signal(int)
    acquisition_requested = Signal(int)
    background_requested = Signal(int)
    reference_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("deviceSidebar"); self.setMinimumWidth(290); self.setMaximumWidth(370)
        self.cards = {}; self.selected_device_id = None
        self._scan_locked = False
        layout = QtWidgets.QVBoxLayout(self); layout.setContentsMargins(8, 8, 8, 8)
        title = QtWidgets.QLabel("设备")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        self.scroll = QtWidgets.QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        container = QtWidgets.QWidget(); self.card_layout = QtWidgets.QVBoxLayout(container)
        self.card_layout.setContentsMargins(0, 4, 0, 4); self.card_layout.setSpacing(7); self.card_layout.addStretch(1)
        self.scroll.setWidget(container); layout.addWidget(self.scroll, 1)

    def add_or_update_device(self, device):
        card = self.cards.get(device.device_id)
        if card is None:
            card = DeviceCard(device); self.cards[device.device_id] = card
            card.selected.connect(self.select_device); card.enabled_changed.connect(self.enabled_changed)
            card.integration_changed.connect(self.integration_changed)
            card.parameters_requested.connect(self.parameters_requested)
            card.disconnect_requested.connect(self.disconnect_requested)
            card.acquisition_requested.connect(self.acquisition_requested)
            card.background_requested.connect(self.background_requested)
            card.reference_requested.connect(self.reference_requested)
            card.set_scan_locked(self._scan_locked)
            self.card_layout.insertWidget(self.card_layout.count() - 1, card)
        else:
            card.update_device(device)
        if self.selected_device_id is None:
            self.select_device(device.device_id)

    def remove_device(self, device_id):
        card = self.cards.pop(device_id, None)
        if card:
            self.card_layout.removeWidget(card); card.deleteLater()
        if self.selected_device_id == device_id:
            self.selected_device_id = None
            if self.cards: self.select_device(next(iter(self.cards)))

    def select_device(self, device_id):
        if device_id not in self.cards: return
        self.selected_device_id = device_id
        for key, card in self.cards.items(): card.set_selected(key == device_id)
        self.selection_changed.emit(device_id)

    def set_device_control_state(self, device_id: int, state: str):
        card = self.cards.get(device_id)
        if card:
            card.set_acquisition_state(state)

    def set_scan_locked(self, locked: bool):
        self._scan_locked = bool(locked)
        for card in self.cards.values():
            card.set_scan_locked(self._scan_locked)
