"""单设备完整参数与校准对话框。"""

import math
from pathlib import Path

import numpy as np

from ..processing.intensity_calibration import (
    IntensityCalibrationError,
    parse_intensity_calibration_bytes,
)
from ..processing.profiles import (
    AIRPLS_ITER_MAX,
    AIRPLS_ITER_MIN,
    AIRPLS_LAM_MAX,
    AIRPLS_LAM_MIN,
    AIRPLS_ORDER_MAX,
    AIRPLS_ORDER_MIN,
    AirplsProfile,
    DeviceAirplsOverride,
    ProfileValidationError,
    resolve_effective_profile,
)
from ..qt import QtCore, QtWidgets
from .input_controls import DirectDoubleSpinBox, DirectSpinBox, NoWheelComboBox


class DeviceParametersDialog(QtWidgets.QDialog):
    def __init__(
        self,
        device,
        device_manager,
        parent=None,
        *,
        global_airpls_profile=None,
        calibration_repository=None,
        profile_repository=None,
        can_modify=None,
        diagnostic_callback=None,
    ):
        super().__init__(parent)
        self.device = device; self.manager = device_manager
        self.global_airpls_profile = (
            global_airpls_profile or AirplsProfile()
        )
        self.calibration_repository = calibration_repository
        self.profile_repository = profile_repository
        self.can_modify = can_modify or (lambda: not self.device.acquiring)
        self.diagnostic_callback = diagnostic_callback or (
            lambda _message, _level="INFO": None
        )
        self.setWindowTitle(f"{device.port_name} 设备参数"); self.setMinimumWidth(500)
        form = QtWidgets.QFormLayout(self)
        info = device.info
        form.addRow("设备", QtWidgets.QLabel(f"{device.port_name} / {info.prod_serial or info.serial_num or '未识别'}"))
        form.addRow("固件 / 硬件", QtWidgets.QLabel(f"{info.fw_ver:.2f} / {info.hw_ver:.2f}"))
        form.addRow("像素", QtWidgets.QLabel(f"总数 {info.pixel_count}，起始 {info.start_pixel}，有效 {info.valid_pixel}"))
        self.integration = self._spin(device.integration_time_us, 1, 99_999_999); form.addRow("积分时间 (µs)", self.integration)
        self.trigger = NoWheelComboBox(); self.trigger.addItems(["软件触发", "软触发主机", "外部触发"])
        self.trigger.setCurrentIndex(max(0, min(2, device.trigger_mode))); form.addRow("触发模式", self.trigger)
        self.interval = self._spin(device.interval_us, 0, 99_999_999); form.addRow("采集间隔 (µs)", self.interval)
        self.average = self._spin(device.avg_count, 1, 10_000); form.addRow("平均次数", self.average)
        self.gain = self._spin(device.gain, 0, 63); form.addRow("增益", self.gain)
        self.delay = self._spin(device.delay_us, 0, 99_999_999); form.addRow("触发延时 (µs)", self.delay)
        form.addRow(QtWidgets.QLabel("波长校准（上位机顺序 C1→C4，发送时自动反序）"))
        self.calibration = []
        for name, value in zip(("C1（0 阶）", "C2（1 阶）", "C3（2 阶）", "C4（3 阶）"),
                               (device.wavelength_calib.c1, device.wavelength_calib.c2, device.wavelength_calib.c3, device.wavelength_calib.c4)):
            spin = DirectDoubleSpinBox(); spin.setDecimals(15); spin.setRange(-1e9, 1e9); spin.setValue(value)
            self.calibration.append(spin); form.addRow(name, spin)

        form.addRow(self._separator("强度校准"))
        self.intensity_calibration_status = QtWidgets.QLabel()
        self.intensity_calibration_status.setWordWrap(True)
        form.addRow("当前状态", self.intensity_calibration_status)
        calibration_buttons = QtWidgets.QWidget()
        calibration_layout = QtWidgets.QHBoxLayout(calibration_buttons)
        calibration_layout.setContentsMargins(0, 0, 0, 0)
        self.import_intensity_calibration = QtWidgets.QPushButton(
            "导入 CSV / TXT"
        )
        self.clear_intensity_calibration = QtWidgets.QPushButton("清除")
        self.import_intensity_calibration.clicked.connect(
            self._import_intensity_calibration
        )
        self.clear_intensity_calibration.clicked.connect(
            self._clear_intensity_calibration
        )
        calibration_layout.addWidget(self.import_intensity_calibration)
        calibration_layout.addWidget(self.clear_intensity_calibration)
        calibration_layout.addStretch(1)
        form.addRow("校准文件", calibration_buttons)

        form.addRow(self._separator("airPLS 基线校正"))
        override = device.airpls_override or DeviceAirplsOverride()
        self.airpls_follow_global = QtWidgets.QCheckBox("跟随全局设置")
        self.airpls_follow_global.setChecked(override.follow_global)
        form.addRow("配置来源", self.airpls_follow_global)
        self.airpls_enabled = QtWidgets.QCheckBox("启用 airPLS")
        self.airpls_enabled.setChecked(override.enabled)
        form.addRow("状态", self.airpls_enabled)
        self.airpls_lam = DirectDoubleSpinBox()
        self.airpls_lam.setDecimals(6)
        self.airpls_lam.setRange(AIRPLS_LAM_MIN, AIRPLS_LAM_MAX)
        self.airpls_lam.setValue(override.lam)
        form.addRow("平滑参数 λ", self.airpls_lam)
        self.airpls_order = DirectSpinBox()
        self.airpls_order.setRange(AIRPLS_ORDER_MIN, AIRPLS_ORDER_MAX)
        self.airpls_order.setValue(override.order)
        form.addRow("差分阶数", self.airpls_order)
        self.airpls_max_iter = DirectSpinBox()
        self.airpls_max_iter.setRange(AIRPLS_ITER_MIN, AIRPLS_ITER_MAX)
        self.airpls_max_iter.setValue(override.max_iter)
        form.addRow("最大迭代次数", self.airpls_max_iter)
        self.airpls_effective = QtWidgets.QLabel()
        self.airpls_effective.setWordWrap(True)
        form.addRow("最终生效", self.airpls_effective)
        self.airpls_follow_global.toggled.connect(
            self._refresh_airpls_controls
        )
        self.airpls_enabled.toggled.connect(self._refresh_airpls_status)
        self.airpls_lam.valueChanged.connect(self._refresh_airpls_status)
        self.airpls_order.valueChanged.connect(self._refresh_airpls_status)
        self.airpls_max_iter.valueChanged.connect(self._refresh_airpls_status)
        self._refresh_intensity_calibration_status()
        self._refresh_airpls_controls()

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Apply | QtWidgets.QDialogButtonBox.Close)
        buttons.button(QtWidgets.QDialogButtonBox.Apply).clicked.connect(self.apply)
        buttons.rejected.connect(self.reject); form.addRow(buttons)

    @staticmethod
    def _spin(value, minimum, maximum):
        widget = DirectSpinBox(); widget.setRange(minimum, maximum); widget.setValue(value); return widget

    @staticmethod
    def _separator(text):
        label = QtWidgets.QLabel(text)
        label.setStyleSheet("font-weight: 600; margin-top: 8px;")
        return label

    def _serial_and_pixels(self):
        serial = str(self.device.info.prod_serial or "").strip()
        pixels = int(
            self.device.info.valid_pixel
            or self.device.info.pixel_count
            or 0
        )
        return serial, pixels

    def _refresh_intensity_calibration_status(self):
        record = self.device.intensity_calibration_record
        if record is None:
            text = "未启用（原始强度系数视为 1）"
        else:
            text = (
                f"已启用：{record.pixel_count} 点，"
                f"ID {record.calibration_id}，源文件 {record.source_name}"
            )
        self.intensity_calibration_status.setText(text)
        self.clear_intensity_calibration.setEnabled(record is not None)

    def _refresh_airpls_controls(self):
        editable = not self.airpls_follow_global.isChecked()
        for widget in (
            self.airpls_enabled,
            self.airpls_lam,
            self.airpls_order,
            self.airpls_max_iter,
        ):
            widget.setEnabled(editable)
        self._refresh_airpls_status()

    def _pending_airpls_override(self):
        return DeviceAirplsOverride(
            follow_global=self.airpls_follow_global.isChecked(),
            enabled=self.airpls_enabled.isChecked(),
            lam=self.airpls_lam.value(),
            order=self.airpls_order.value(),
            max_iter=self.airpls_max_iter.value(),
        )

    def _refresh_airpls_status(self, *_args):
        try:
            profile = resolve_effective_profile(
                self.global_airpls_profile,
                self._pending_airpls_override(),
            )
            source = "全局" if profile.source == "global" else "本设备"
            state = "启用" if profile.enabled else "停用"
            self.airpls_effective.setText(
                f"{source} / {state} / λ={profile.lam:g} / "
                f"阶数={profile.order} / 迭代={profile.max_iter}"
            )
        except ProfileValidationError as exc:
            self.airpls_effective.setText(str(exc))

    def _reject_busy_change(self):
        if self.can_modify():
            return False
        message = "设备正在采集、停止或保存，暂时不能修改处理配置"
        self.diagnostic_callback(message, "WARN")
        QtWidgets.QMessageBox.warning(self, "操作被拒绝", message)
        return True

    def _import_intensity_calibration(self):
        if self._reject_busy_change():
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "导入强度校准",
            "",
            "强度校准文件 (*.csv *.txt);;所有文件 (*)",
        )
        if not path:
            return
        serial, pixels = self._serial_and_pixels()
        try:
            record = parse_intensity_calibration_bytes(
                Path(path).read_bytes(),
                device_serial=serial,
                pixel_count=pixels,
                source_name=Path(path).name,
            )
            if serial and self.calibration_repository is not None:
                self.calibration_repository.save(record)
            self.device.intensity_calibration_record = record
            self.device.intensity_calib = np.asarray(
                record.coefficients, dtype=np.float64
            )
        except (OSError, IntensityCalibrationError, RuntimeError) as exc:
            message = f"强度校准导入失败：{exc}"
            self.diagnostic_callback(message, "WARN")
            QtWidgets.QMessageBox.warning(self, "导入失败", message)
            return
        self._refresh_intensity_calibration_status()
        persistence = "并已绑定设备序列号" if serial else "（仅当前运行有效）"
        self.diagnostic_callback(
            f"设备 {self.device.device_id} 已导入强度校准 "
            f"{record.calibration_id}{persistence}"
        )

    def _clear_intensity_calibration(self):
        if self._reject_busy_change():
            return
        serial, pixels = self._serial_and_pixels()
        try:
            if serial and self.calibration_repository is not None:
                self.calibration_repository.clear(serial, pixels)
        except RuntimeError as exc:
            message = f"强度校准清除失败：{exc}"
            self.diagnostic_callback(message, "WARN")
            QtWidgets.QMessageBox.warning(self, "清除失败", message)
            return
        self.device.intensity_calibration_record = None
        self.device.intensity_calib = None
        self._refresh_intensity_calibration_status()
        self.diagnostic_callback(
            f"设备 {self.device.device_id} 的强度校准已清除"
        )

    def apply(self):
        if self._reject_busy_change():
            return
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

        try:
            override = self._pending_airpls_override()
            serial, _pixels = self._serial_and_pixels()
            if serial and self.profile_repository is not None:
                self.profile_repository.save(serial, override)
            self.device.airpls_override = override
        except (ProfileValidationError, RuntimeError) as exc:
            message = f"设备 airPLS 配置保存失败：{exc}"
            self.diagnostic_callback(message, "WARN")
            QtWidgets.QMessageBox.warning(self, "保存失败", message)
            return
        persistence = "并已绑定设备序列号" if serial else "（仅当前运行有效）"
        self.diagnostic_callback(
            f"设备 {device_id} 的 airPLS 配置已保存{persistence}"
        )
        self._refresh_airpls_status()
