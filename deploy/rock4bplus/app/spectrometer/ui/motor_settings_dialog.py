"""LK-MD2202 connection and two-axis configuration dialog."""

from __future__ import annotations

from dataclasses import replace

from ..motor.lk_md2202 import (
    BAUD_RATE_TO_CODE,
    AxisConfiguration,
    CommunicationConfiguration,
    DeviceConfiguration,
    LimitMode,
    Microstep,
    RunCurrent,
    StepAngle,
    StopCurrent,
)
from ..motor.settings_store import HostMotorSettings
from ..qt import QtWidgets, Signal
from .input_controls import DirectSpinBox, NoWheelComboBox


_STEP_ANGLE_LABELS = {
    StepAngle.DEG_1_8: "1.8°",
    StepAngle.DEG_0_9: "0.9°",
}
_RUN_CURRENT_LABELS = {
    RunCurrent.P100: "100%",
    RunCurrent.P87_5: "87.5%",
    RunCurrent.P75: "75%",
    RunCurrent.P62_5: "62.5%",
    RunCurrent.P50: "50%",
    RunCurrent.P37_5: "37.5%",
    RunCurrent.P25: "25%",
    RunCurrent.P12_5: "12.5%",
    RunCurrent.OFF: "关闭",
}


def configuration_differences(current, desired, old_host, new_host):
    """Return concise, user-visible staged changes for confirmation."""
    differences = []
    if current is not None:
        labels = {
            "step_angle": "步距角",
            "microstep": "细分",
            "run_current": "运行电流",
            "limit_mode": "限位模式",
            "end_position_pulses": "终点位置",
            "stop_current": "停止电流",
            "acceleration": "加速参数",
            "deceleration": "减速参数",
            "position_speed_pps": "位置速度",
        }
        for axis_name in ("x", "y"):
            before, after = getattr(current, axis_name), getattr(desired, axis_name)
            for field, label in labels.items():
                old, new = getattr(before, field), getattr(after, field)
                if old != new:
                    differences.append(
                        f"{axis_name.upper()}/{('M1' if axis_name == 'x' else 'M2')} "
                        f"{label}：{old} → {new}"
                    )
        for field, label in (("address", "Modbus 地址"), ("baud_rate", "波特率")):
            old = getattr(current.communication, field)
            new = getattr(desired.communication, field)
            if old != new:
                differences.append(f"{label}：{old} → {new}")
    for field, label in (
        ("automatic_port", "自动串口"),
        ("port_name", "手动串口"),
        ("reverse_x", "X 方向反转"),
        ("reverse_y", "Y 方向反转"),
    ):
        old, new = getattr(old_host, field), getattr(new_host, field)
        if old != new:
            differences.append(f"{label}：{old} → {new}")
    return tuple(differences)


def _combo(items):
    control = NoWheelComboBox()
    for label, value in items:
        control.addItem(label, int(value))
    return control


def _set_combo_value(control, value):
    index = control.findData(int(value))
    control.setCurrentIndex(max(0, index))


class MotorSettingsDialog(QtWidgets.QDialog):
    """Edit staged values; applying is performed asynchronously by the controller."""

    apply_requested = Signal(object, object)

    def __init__(
        self,
        host_settings: HostMotorSettings,
        device_configuration: DeviceConfiguration | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("LK-MD2202 电机设置")
        self.setMinimumSize(760, 610)
        self._busy = False
        self._device_configuration = device_configuration or DeviceConfiguration(
            communication=CommunicationConfiguration(
                address=host_settings.address,
                baud_rate=host_settings.baud_rate,
            )
        )

        outer = QtWidgets.QVBoxLayout(self)
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self._build_connection_page(), "连接")
        self.tabs.addTab(self._build_axis_page(), "X/M1、Y/M2 参数")
        outer.addWidget(self.tabs, 1)

        self.result_label = QtWidgets.QLabel(
            "连接设备后可将差异参数写入驱动板并自动回读校验。"
        )
        self.result_label.setWordWrap(True)
        self.result_label.setObjectName("motorSettingsResult")
        outer.addWidget(self.result_label)

        buttons = QtWidgets.QHBoxLayout()
        self.defaults_button = QtWidgets.QPushButton("恢复建议默认值")
        self.defaults_button.setToolTip("只填入表单，不会立即写入驱动板")
        self.defaults_button.clicked.connect(self.restore_recommended_defaults)
        buttons.addWidget(self.defaults_button)
        buttons.addStretch(1)
        self.apply_button = QtWidgets.QPushButton("应用")
        self.apply_button.clicked.connect(self._request_apply)
        buttons.addWidget(self.apply_button)
        close_button = QtWidgets.QPushButton("关闭")
        close_button.clicked.connect(self.reject)
        buttons.addWidget(close_button)
        outer.addLayout(buttons)

        self.set_values(host_settings, self._device_configuration)

    def _build_connection_page(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QFormLayout(page)
        self.automatic_port = QtWidgets.QCheckBox("自动识别 USB-RS485 串口")
        self.automatic_port.toggled.connect(self._update_port_state)
        layout.addRow("串口方式", self.automatic_port)
        self.port_name = QtWidgets.QLineEdit()
        self.port_name.setPlaceholderText("例如 COM7 或 /dev/ttyUSB0")
        layout.addRow("手动串口", self.port_name)
        self.address = DirectSpinBox()
        self.address.setRange(1, 247)
        layout.addRow("Modbus 地址", self.address)
        self.baud_rate = NoWheelComboBox()
        for rate in BAUD_RATE_TO_CODE:
            self.baud_rate.addItem(str(rate), rate)
        layout.addRow("波特率", self.baud_rate)
        protocol = QtWidgets.QLabel("8 数据位，1 停止位，无校验（8N1，只读）")
        layout.addRow("串口格式", protocol)
        note = QtWidgets.QLabel(
            "自动识别只执行只读身份校验；修改地址或波特率时会最后写入，"
            "随后用新参数重新连接并验证设备。"
        )
        note.setWordWrap(True)
        layout.addRow("安全说明", note)
        return page

    def _build_axis_page(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QGridLayout(page)
        layout.addWidget(QtWidgets.QLabel("参数"), 0, 0)
        layout.addWidget(QtWidgets.QLabel("X / M1"), 0, 1)
        layout.addWidget(QtWidgets.QLabel("Y / M2"), 0, 2)
        self._axis_controls = {}
        labels = (
            ("step_angle", "步距角"),
            ("microstep", "细分"),
            ("run_current", "运行电流"),
            ("limit_mode", "限位模式"),
            ("end_position_pulses", "终点位置 pulse"),
            ("stop_current", "停止电流"),
            ("acceleration", "加速参数"),
            ("deceleration", "减速参数"),
            ("position_speed_pps", "位置速度 pps"),
            ("reverse", "上位机方向反转"),
        )
        for row, (field, label) in enumerate(labels, start=1):
            layout.addWidget(QtWidgets.QLabel(label), row, 0)
            for column, axis in enumerate(("x", "y"), start=1):
                control = self._axis_control(field)
                layout.addWidget(control, row, column)
                self._axis_controls[(axis, field)] = control

        mechanics = QtWidgets.QLabel(
            "机械换算固定：15 mm = 4800 pulse = 320 pulse/mm；"
            "步距角、细分和终点位置作为一组锁定，防止毫米换算失配。"
        )
        mechanics.setWordWrap(True)
        mechanics.setObjectName("motorHint")
        layout.addWidget(mechanics, len(labels) + 1, 0, 1, 3)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(2, 1)
        return page

    def _axis_control(self, field):
        if field == "step_angle":
            control = _combo(
                (_STEP_ANGLE_LABELS[value], value) for value in StepAngle
            )
            control.setEnabled(False)
            return control
        if field == "microstep":
            control = _combo((str(value.divisor), value) for value in Microstep)
            control.setEnabled(False)
            return control
        if field == "run_current":
            return _combo((_RUN_CURRENT_LABELS[value], value) for value in RunCurrent)
        if field == "limit_mode":
            control = _combo((("零点限位", LimitMode.ZERO_POINT),))
            control.setEnabled(False)
            return control
        if field == "stop_current":
            return _combo((("无锁定", StopCurrent.UNLOCKED), ("保持", StopCurrent.HOLDING)))
        if field == "reverse":
            return QtWidgets.QCheckBox("反转")
        control = DirectSpinBox()
        if field == "end_position_pulses":
            control.setRange(4800, 4800)
            control.setEnabled(False)
        elif field in ("acceleration", "deceleration"):
            control.setRange(1, 65535)
        else:
            control.setRange(1, 1_000_000)
        return control

    def _update_port_state(self, automatic):
        self.port_name.setEnabled(not automatic)

    def set_values(self, host, configuration):
        self.automatic_port.setChecked(host.automatic_port)
        self.port_name.setText(host.port_name)
        self.address.setValue(configuration.communication.address)
        index = self.baud_rate.findData(configuration.communication.baud_rate)
        self.baud_rate.setCurrentIndex(max(0, index))
        for axis_name, axis_config, reverse in (
            ("x", configuration.x, host.reverse_x),
            ("y", configuration.y, host.reverse_y),
        ):
            for field in (
                "step_angle",
                "microstep",
                "run_current",
                "limit_mode",
                "stop_current",
            ):
                _set_combo_value(
                    self._axis_controls[(axis_name, field)],
                    getattr(axis_config, field),
                )
            for field in (
                "end_position_pulses",
                "acceleration",
                "deceleration",
                "position_speed_pps",
            ):
                self._axis_controls[(axis_name, field)].setValue(
                    int(getattr(axis_config, field))
                )
            self._axis_controls[(axis_name, "reverse")].setChecked(reverse)
        self._update_port_state(host.automatic_port)

    def restore_recommended_defaults(self):
        host, _ = self.values()
        host = replace(host, reverse_x=False, reverse_y=False)
        configuration = DeviceConfiguration(
            communication=CommunicationConfiguration(
                address=self.address.value(),
                baud_rate=int(self.baud_rate.currentData()),
            )
        )
        self.set_values(host, configuration)
        self.result_label.setText(
            "已填入建议默认值；尚未写入驱动板。"
        )

    def _axis_value(self, axis_name):
        controls = self._axis_controls
        return AxisConfiguration(
            step_angle=controls[(axis_name, "step_angle")].currentData(),
            microstep=controls[(axis_name, "microstep")].currentData(),
            run_current=controls[(axis_name, "run_current")].currentData(),
            limit_mode=controls[(axis_name, "limit_mode")].currentData(),
            end_position_pulses=controls[(axis_name, "end_position_pulses")].value(),
            stop_current=controls[(axis_name, "stop_current")].currentData(),
            acceleration=controls[(axis_name, "acceleration")].value(),
            deceleration=controls[(axis_name, "deceleration")].value(),
            position_speed_pps=controls[(axis_name, "position_speed_pps")].value(),
        )

    def values(self):
        host = HostMotorSettings(
            automatic_port=self.automatic_port.isChecked(),
            port_name=self.port_name.text().strip(),
            address=self.address.value(),
            baud_rate=int(self.baud_rate.currentData()),
            reverse_x=self._axis_controls[("x", "reverse")].isChecked(),
            reverse_y=self._axis_controls[("y", "reverse")].isChecked(),
        )
        configuration = DeviceConfiguration(
            x=self._axis_value("x"),
            y=self._axis_value("y"),
            communication=CommunicationConfiguration(
                address=host.address,
                baud_rate=host.baud_rate,
            ),
        )
        return host, configuration

    def _request_apply(self):
        host, configuration = self.values()
        self.set_busy(True)
        self.result_label.setText("正在应用并回读校验…")
        self.apply_requested.emit(host, configuration)

    def set_busy(self, busy):
        self._busy = bool(busy)
        self.apply_button.setEnabled(not self._busy)
        self.defaults_button.setEnabled(not self._busy)
        self.tabs.setEnabled(not self._busy)

    def set_apply_result(self, success, message):
        self.set_busy(False)
        self.result_label.setText(str(message))
        self.result_label.setProperty("state", "success" if success else "error")
        self.result_label.style().unpolish(self.result_label)
        self.result_label.style().polish(self.result_label)

    def reject(self):
        if self._busy:
            self.result_label.setText("配置事务尚未完成，暂时不能关闭。")
            return
        super().reject()
