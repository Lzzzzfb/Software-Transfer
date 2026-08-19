"""Host-side connection settings for the STM32 square-wave generator."""

from __future__ import annotations

from dataclasses import replace

from ..qt import QtWidgets, Signal
from ..square_wave.models import DEFAULT_BAUD_RATE, DeviceIdentity
from ..square_wave.settings_store import HostSquareWaveSettings
from .input_controls import NoWheelComboBox


class SquareWaveSettingsDialog(QtWidgets.QDialog):
    apply_requested = Signal(object)
    refresh_requested = Signal()

    def __init__(
        self,
        host_settings: HostSquareWaveSettings,
        *,
        identity: DeviceIdentity | None = None,
        serial_number="",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("方波发生器设置")
        self.setMinimumSize(610, 360)
        self._host_settings = host_settings

        outer = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()

        self.automatic_port = QtWidgets.QCheckBox("自动识别 STM32 USB 串口")
        self.automatic_port.toggled.connect(self._update_port_state)
        form.addRow("串口方式", self.automatic_port)

        port_row = QtWidgets.QHBoxLayout()
        self.port_name = NoWheelComboBox()
        self.port_name.setEditable(True)
        self.port_name.setInsertPolicy(
            QtWidgets.QComboBox.InsertPolicy.NoInsert
        )
        port_row.addWidget(self.port_name, 1)
        self.refresh_button = QtWidgets.QPushButton("刷新候选")
        self.refresh_button.clicked.connect(self.refresh_requested)
        port_row.addWidget(self.refresh_button)
        form.addRow("手动串口", port_row)

        self.baud_rate = QtWidgets.QLabel(str(DEFAULT_BAUD_RATE))
        form.addRow("波特率", self.baud_rate)
        self.serial_format = QtWidgets.QLabel(
            "8 数据位，1 停止位，无校验（8N1）"
        )
        form.addRow("串口格式", self.serial_format)
        self.identity_name = QtWidgets.QLabel(
            identity.name if identity is not None else "未确认"
        )
        form.addRow("设备身份", self.identity_name)
        self.protocol_version = QtWidgets.QLabel(
            str(identity.protocol_version) if identity is not None else "未确认"
        )
        form.addRow("协议版本", self.protocol_version)
        self.usb_serial = QtWidgets.QLabel(str(serial_number or "未提供"))
        form.addRow("USB 序列号", self.usb_serial)
        outer.addLayout(form)

        note = QtWidgets.QLabel(
            "自动识别只用 ID? 确认设备；连接后会读取 STATUS?。"
            "扫描联动是本次软件运行的临时选择，不会保存到设置文件。"
        )
        note.setWordWrap(True)
        note.setObjectName("motorHint")
        outer.addWidget(note)
        outer.addStretch(1)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        self.apply_button = QtWidgets.QPushButton("应用")
        self.apply_button.clicked.connect(self._request_apply)
        buttons.addWidget(self.apply_button)
        close_button = QtWidgets.QPushButton("关闭")
        close_button.clicked.connect(self.reject)
        buttons.addWidget(close_button)
        outer.addLayout(buttons)

        self.automatic_port.setChecked(host_settings.automatic_port)
        initial_port = host_settings.system_location or host_settings.port_name
        if initial_port:
            self.port_name.addItem(initial_port)
            self.port_name.setCurrentText(initial_port)
        self._update_port_state()

    def set_candidates(self, candidates):
        current = self.port_name.currentText().strip()
        self.port_name.blockSignals(True)
        self.port_name.clear()
        for candidate in tuple(candidates or ()):
            location = candidate.system_location or candidate.port_name
            label = location
            if candidate.serial_number:
                label += f"  [{candidate.serial_number}]"
            self.port_name.addItem(label, location)
        if current:
            index = self.port_name.findData(current)
            if index >= 0:
                self.port_name.setCurrentIndex(index)
            else:
                self.port_name.setEditText(current)
        self.port_name.blockSignals(False)

    def _update_port_state(self):
        self.port_name.setEnabled(not self.automatic_port.isChecked())

    def values(self):
        current_text = self.port_name.currentText().strip()
        current_index = self.port_name.currentIndex()
        if (
            current_index >= 0
            and current_text == self.port_name.itemText(current_index)
        ):
            port_name = self.port_name.itemData(current_index)
        else:
            port_name = current_text
        if "  [" in str(port_name):
            port_name = str(port_name).split("  [", 1)[0]
        automatic = bool(self.automatic_port.isChecked())
        return replace(
            self._host_settings,
            automatic_port=automatic,
            port_name=str(port_name or ""),
            system_location=(
                self._host_settings.system_location if automatic else ""
            ),
            usb_serial=(self._host_settings.usb_serial if automatic else ""),
        )

    def _request_apply(self):
        self.apply_requested.emit(self.values())
