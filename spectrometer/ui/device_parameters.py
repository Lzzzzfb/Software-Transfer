"""单设备完整参数与校准对话框。"""

import math

from ..qt import QtCore, QtWidgets


class DeviceParametersDialog(QtWidgets.QDialog):
    def __init__(self, device, device_manager, parent=None):
        super().__init__(parent)
        self.device = device; self.manager = device_manager
        self.setWindowTitle(f"{device.port_name} 设备参数"); self.setMinimumWidth(500)
        form = QtWidgets.QFormLayout(self)
        info = device.info
        form.addRow("设备", QtWidgets.QLabel(f"{device.port_name} / {info.prod_serial or info.serial_num or '未识别'}"))
        form.addRow("固件 / 硬件", QtWidgets.QLabel(f"{info.fw_ver:.2f} / {info.hw_ver:.2f}"))
        form.addRow("像素", QtWidgets.QLabel(f"总数 {info.pixel_count}，起始 {info.start_pixel}，有效 {info.valid_pixel}"))
        self.integration = self._spin(device.integration_time_us, 1, 99_999_999); form.addRow("积分时间 (µs)", self.integration)
        self.trigger = QtWidgets.QComboBox(); self.trigger.addItems(["软件触发", "软触发主机", "外部触发"])
        self.trigger.setCurrentIndex(max(0, min(2, device.trigger_mode))); form.addRow("触发模式", self.trigger)
        self.interval = self._spin(device.interval_us, 0, 99_999_999); form.addRow("采集间隔 (µs)", self.interval)
        self.average = self._spin(device.avg_count, 1, 10_000); form.addRow("平均次数", self.average)
        self.gain = self._spin(device.gain, 0, 63); form.addRow("增益", self.gain)
        self.delay = self._spin(device.delay_us, 0, 99_999_999); form.addRow("触发延时 (µs)", self.delay)
        form.addRow(QtWidgets.QLabel("波长校准（上位机顺序 C1→C4，发送时自动反序）"))
        self.calibration = []
        for name, value in zip(("C1（0 阶）", "C2（1 阶）", "C3（2 阶）", "C4（3 阶）"),
                               (device.wavelength_calib.c1, device.wavelength_calib.c2, device.wavelength_calib.c3, device.wavelength_calib.c4)):
            spin = QtWidgets.QDoubleSpinBox(); spin.setDecimals(15); spin.setRange(-1e9, 1e9); spin.setValue(value)
            self.calibration.append(spin); form.addRow(name, spin)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Apply | QtWidgets.QDialogButtonBox.Close)
        buttons.button(QtWidgets.QDialogButtonBox.Apply).clicked.connect(self.apply)
        buttons.rejected.connect(self.reject); form.addRow(buttons)

    @staticmethod
    def _spin(value, minimum, maximum):
        widget = QtWidgets.QSpinBox(); widget.setRange(minimum, maximum); widget.setValue(value); return widget

    def apply(self):
        device_id = self.device.device_id
        actions = []
        values = (
            (self.integration.value(), self.device.integration_time_us, self.manager.set_integration_time),
            (self.trigger.currentIndex(), self.device.trigger_mode, self.manager.set_trigger_mode),
            (self.interval.value(), self.device.interval_us, self.manager.set_interval),
            (self.average.value(), self.device.avg_count, self.manager.set_avg_count),
            (self.gain.value(), self.device.gain, self.manager.set_gain),
            (self.delay.value(), self.device.delay_us, self.manager.set_delay),
        )
        for new_value, old_value, setter in values:
            if new_value != old_value:
                actions.append(lambda s=setter, value=new_value: s(device_id, value))

        calibration = tuple(widget.value() for widget in self.calibration)
        current = tuple(
            getattr(self.device.wavelength_calib, name) for name in ("c1", "c2", "c3", "c4")
        )
        if any(not math.isclose(new, old, rel_tol=1e-12, abs_tol=1e-15)
               for new, old in zip(calibration, current)):
            actions.append(lambda values=calibration: self.manager.set_calib_coeff(device_id, *values))

        # The real controller can drop back-to-back writes.  Pace only changed
        # values so every command has time to return its ACK before the next one.
        for index, action in enumerate(actions):
            QtCore.QTimer.singleShot(index * 120, action)
