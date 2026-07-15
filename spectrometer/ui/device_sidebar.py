"""左侧总控和设备卡片。"""

from ..qt import QtCore, QtWidgets, Signal


class DeviceCard(QtWidgets.QFrame):
    selected = Signal(int)
    enabled_changed = Signal(int, bool)
    integration_changed = Signal(int, int)
    parameters_requested = Signal(int)
    disconnect_requested = Signal(int)

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
        self.enabled = QtWidgets.QCheckBox("启用"); self.enabled.setChecked(True)
        self.enabled.toggled.connect(lambda value: self.enabled_changed.emit(self.device_id, value))
        top.addWidget(self.enabled)
        disconnect = QtWidgets.QToolButton(); disconnect.setText("×"); disconnect.setToolTip("断开并移除设备")
        disconnect.clicked.connect(lambda: self.disconnect_requested.emit(self.device_id)); top.addWidget(disconnect)
        layout.addLayout(top)
        self.details = QtWidgets.QLabel(); self.details.setObjectName("deviceDetails"); layout.addWidget(self.details)
        controls = QtWidgets.QHBoxLayout(); controls.addWidget(QtWidgets.QLabel("积分 µs"))
        self.integration = QtWidgets.QSpinBox(); self.integration.setRange(1, 99_999_999)
        self.integration.setSingleStep(1000); self.integration.setValue(device.integration_time_us)
        controls.addWidget(self.integration, 1)
        apply_button = QtWidgets.QPushButton("应用")
        apply_button.clicked.connect(lambda: self.integration_changed.emit(self.device_id, self.integration.value()))
        controls.addWidget(apply_button); layout.addLayout(controls)
        parameters = QtWidgets.QPushButton("设备参数与校准…")
        parameters.clicked.connect(lambda: self.parameters_requested.emit(self.device_id))
        layout.addWidget(parameters)
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


class DeviceSidebar(QtWidgets.QWidget):
    selection_changed = Signal(int)
    integration_changed = Signal(int, int)
    enabled_changed = Signal(int, bool)
    parameters_requested = Signal(int)
    connect_requested = Signal(str, int)
    disconnect_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("deviceSidebar"); self.setMinimumWidth(290); self.setMaximumWidth(370)
        self.cards = {}; self.selected_device_id = None
        layout = QtWidgets.QVBoxLayout(self); layout.setContentsMargins(8, 8, 8, 8)
        title = QtWidgets.QLabel("设备与批量存储"); title.setObjectName("sectionTitle"); layout.addWidget(title)
        connection_box = QtWidgets.QFrame(); connection_box.setObjectName("storageBox")
        connection_layout = QtWidgets.QGridLayout(connection_box); connection_layout.setContentsMargins(10, 8, 10, 8)
        connection_layout.addWidget(QtWidgets.QLabel("串口"), 0, 0)
        self.port_combo = QtWidgets.QComboBox(); connection_layout.addWidget(self.port_combo, 0, 1)
        self.refresh_button = QtWidgets.QPushButton("刷新"); connection_layout.addWidget(self.refresh_button, 0, 2)
        connection_layout.addWidget(QtWidgets.QLabel("波特率"), 1, 0)
        self.baud_combo = QtWidgets.QComboBox(); self.baud_combo.setEditable(True)
        self.baud_combo.addItems(["115200", "230400", "460800", "921600"]); connection_layout.addWidget(self.baud_combo, 1, 1)
        connect_button = QtWidgets.QPushButton("手动连接"); connect_button.clicked.connect(self._manual_connect)
        connection_layout.addWidget(connect_button, 1, 2); layout.addWidget(connection_box)
        storage_box = QtWidgets.QFrame(); storage_box.setObjectName("storageBox")
        form = QtWidgets.QFormLayout(storage_box); form.setContentsMargins(10, 8, 10, 8)
        self.acquisition_mode = QtWidgets.QComboBox(); self.acquisition_mode.addItems(["连续采集", "单次采集"])
        form.addRow("采集方式", self.acquisition_mode)
        self.batch_size = QtWidgets.QSpinBox(); self.batch_size.setRange(1, 1000); self.batch_size.setValue(500)
        form.addRow("每批帧数", self.batch_size)
        self.storage_format = QtWidgets.QComboBox()
        self.storage_format.addItem("CSV + Excel", "csv_excel")
        self.storage_format.addItem("仅 CSV", "csv")
        self.storage_format.addItem("仅 Excel", "excel")
        form.addRow("存储格式", self.storage_format)
        self.auto_store = QtWidgets.QCheckBox("采集时自动批量存储"); self.auto_store.setChecked(True)
        form.addRow(self.auto_store); layout.addWidget(storage_box)
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

    def set_available_ports(self, port_names):
        current = self.port_combo.currentText()
        self.port_combo.blockSignals(True); self.port_combo.clear(); self.port_combo.addItems(port_names)
        index = self.port_combo.findText(current)
        if index >= 0: self.port_combo.setCurrentIndex(index)
        self.port_combo.blockSignals(False)

    def _manual_connect(self):
        port = self.port_combo.currentText().strip()
        if not port: return
        try: baud = int(self.baud_combo.currentText())
        except ValueError: baud = 115200
        self.connect_requested.emit(port, baud)
