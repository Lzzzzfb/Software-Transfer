"""Compact PyQt motor and raster-scan controls for the live workspace."""

from __future__ import annotations

from ..motor.models import (
    MOTOR_SPEED_DEFAULT_HZ,
    MOTOR_SPEED_MAX_HZ,
    MOTOR_SPEED_MIN_HZ,
    MOTOR_TRAVEL_MM,
    Axis,
    Direction,
    MotorStatus,
)
from ..motor.scan import ScanParameters
from ..motor.scan_controller import ScanState
from ..qt import QtCore, QtWidgets, Signal
from .input_controls import DirectDoubleSpinBox, DirectSpinBox


class MotorPanel(QtWidgets.QWidget):
    discover_requested = Signal()
    connect_requested = Signal()
    disconnect_requested = Signal()
    speed_requested = Signal(object, int)
    stall_release_requested = Signal(object, int)
    move_requested = Signal(object, float, object)
    position_clear_requested = Signal(object)
    software_return_requested = Signal(object)
    home_requested = Signal(object)
    stop_requested = Signal()
    clear_fault_requested = Signal()
    scan_start_requested = Signal(object)
    scan_stop_requested = Signal()
    settings_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("motorPanel")
        self.setMinimumHeight(285)
        self._connected = False
        self._motion_active = False
        self._scan_active = False
        self._scan_acquisition_enabled = True
        self._safety_locked = False
        self._axis_controls = {}

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(7)
        outer.addLayout(self._build_connection_row())

        content = QtWidgets.QHBoxLayout()
        content.setSpacing(8)
        content.addWidget(self._build_manual_group(), 3)
        content.addWidget(self._build_scan_group(), 2)
        outer.addLayout(content, 1)
        self._refresh_enabled_state()

    def _build_connection_row(self):
        row = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("电机与扫描")
        title.setObjectName("motorPanelTitle")
        row.addWidget(title)

        self.connection_indicator = QtWidgets.QLabel("未连接")
        self.connection_indicator.setObjectName("motorConnectionState")
        self.connection_indicator.setProperty("state", "offline")
        row.addWidget(self.connection_indicator)

        self.port_label = QtWidgets.QLabel("串口：未识别")
        self.port_label.setObjectName("motorPortLabel")
        row.addWidget(self.port_label)
        row.addStretch(1)

        self.discover_button = QtWidgets.QPushButton("刷新串口")
        self.discover_button.setObjectName("motorDiscoverButton")
        self.discover_button.clicked.connect(self.discover_requested)
        row.addWidget(self.discover_button)
        self.connect_button = QtWidgets.QPushButton("自动识别并连接")
        self.connect_button.setObjectName("motorConnectButton")
        self.connect_button.clicked.connect(self.connect_requested)
        row.addWidget(self.connect_button)
        self.disconnect_button = QtWidgets.QPushButton("断开")
        self.disconnect_button.clicked.connect(self.disconnect_requested)
        row.addWidget(self.disconnect_button)
        self.settings_button = QtWidgets.QPushButton("电机设置")
        self.settings_button.clicked.connect(self.settings_requested)
        row.addWidget(self.settings_button)
        self.stop_button = QtWidgets.QPushButton("急停")
        self.stop_button.setObjectName("motorStopButton")
        self.stop_button.clicked.connect(self.stop_requested)
        row.addWidget(self.stop_button)
        self.clear_fault_button = QtWidgets.QPushButton("清除故障")
        self.clear_fault_button.clicked.connect(self.clear_fault_requested)
        row.addWidget(self.clear_fault_button)
        return row

    def _build_manual_group(self):
        group = QtWidgets.QGroupBox("手动运动与坐标")
        group.setObjectName("motorGroup")
        layout = QtWidgets.QGridLayout(group)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setHorizontalSpacing(6)
        for column, text in enumerate(
            ("轴", "坐标 / 零点限位", "速度 pulse/s", "距离 mm", "运动", "坐标")
        ):
            label = QtWidgets.QLabel(text)
            label.setObjectName("motorColumnHeader")
            layout.addWidget(label, 0, column)

        for row, axis in enumerate((Axis.X, Axis.Y), start=1):
            axis_label = QtWidgets.QLabel(axis.value)
            axis_label.setObjectName("motorAxisLabel")
            layout.addWidget(axis_label, row, 0)

            state_box = QtWidgets.QWidget()
            state_layout = QtWidgets.QHBoxLayout(state_box)
            state_layout.setContentsMargins(0, 0, 0, 0)
            coordinate = QtWidgets.QLabel("--")
            coordinate.setObjectName(f"motor{axis.value}Coordinate")
            limit = QtWidgets.QLabel("限位状态未知")
            limit.setObjectName("motorLimitIndicator")
            limit.setProperty("active", False)
            state_layout.addWidget(coordinate)
            state_layout.addWidget(limit)
            layout.addWidget(state_box, row, 1)

            speed = DirectSpinBox()
            speed.setRange(-MOTOR_SPEED_MAX_HZ, MOTOR_SPEED_MAX_HZ)
            speed.setValue(MOTOR_SPEED_DEFAULT_HZ)
            speed.setSingleStep(100)
            speed.setObjectName(f"motor{axis.value}Speed")
            speed_apply = QtWidgets.QPushButton("设速")
            speed_apply.clicked.connect(
                lambda _checked=False, a=axis, control=speed:
                self.speed_requested.emit(a, control.value())
            )
            speed_box = QtWidgets.QWidget()
            speed_layout = QtWidgets.QHBoxLayout(speed_box)
            speed_layout.setContentsMargins(0, 0, 0, 0)
            speed_layout.addWidget(speed, 1)
            speed_layout.addWidget(speed_apply)
            layout.addWidget(speed_box, row, 2)

            distance = DirectDoubleSpinBox()
            distance.setDecimals(4)
            distance.setRange(1.0 / 320.0, MOTOR_TRAVEL_MM)
            distance.setSingleStep(0.1)
            distance.setValue(1.0)
            distance.setObjectName(f"motor{axis.value}Distance")
            layout.addWidget(distance, row, 3)

            negative = QtWidgets.QPushButton("−")
            negative.setToolTip(f"{axis.value} 轴负向运动")
            negative.clicked.connect(
                lambda _checked=False, a=axis, control=distance:
                self.move_requested.emit(
                    a, control.value(), Direction.NEGATIVE
                )
            )
            positive = QtWidgets.QPushButton("+")
            positive.setToolTip(f"{axis.value} 轴正向运动")
            positive.clicked.connect(
                lambda _checked=False, a=axis, control=distance:
                self.move_requested.emit(
                    a, control.value(), Direction.POSITIVE
                )
            )
            move_box = QtWidgets.QWidget()
            move_layout = QtWidgets.QHBoxLayout(move_box)
            move_layout.setContentsMargins(0, 0, 0, 0)
            move_layout.addWidget(negative)
            move_layout.addWidget(positive)
            layout.addWidget(move_box, row, 4)

            clear = QtWidgets.QPushButton("清零")
            clear.setToolTip("仅将软件坐标设为 0，不执行运动")
            clear.clicked.connect(
                lambda _checked=False, a=axis:
                self.position_clear_requested.emit(a)
            )
            return_zero = QtWidgets.QPushButton("回零")
            return_zero.setToolTip("按可信软件坐标返回 0 mm")
            return_zero.clicked.connect(
                lambda _checked=False, a=axis:
                self.software_return_requested.emit(a)
            )
            home = QtWidgets.QPushButton("机械回零")
            home.setToolTip("触发该轴零点限位并重新建立坐标")
            home.clicked.connect(
                lambda _checked=False, a=axis: self.home_requested.emit(a)
            )
            coordinate_box = QtWidgets.QWidget()
            coordinate_layout = QtWidgets.QGridLayout(coordinate_box)
            coordinate_layout.setContentsMargins(0, 0, 0, 0)
            coordinate_layout.setHorizontalSpacing(4)
            coordinate_layout.setVerticalSpacing(3)
            coordinate_layout.addWidget(clear, 0, 0)
            coordinate_layout.addWidget(return_zero, 0, 1)
            coordinate_layout.addWidget(home, 1, 0)
            stall_release = QtWidgets.QPushButton("脱离卡死")
            stall_release.setToolTip(
                "按当前速度框的带符号速度运行；自动停止后仍需机械回零"
            )
            stall_release.clicked.connect(
                lambda _checked=False, a=axis, control=speed:
                self.stall_release_requested.emit(a, control.value())
            )
            coordinate_layout.addWidget(stall_release, 1, 1)
            layout.addWidget(coordinate_box, row, 5)

            self._axis_controls[axis] = {
                "coordinate": coordinate,
                "limit": limit,
                "speed": speed,
                "speed_apply": speed_apply,
                "stall_release": stall_release,
                "interactive": (
                    speed,
                    speed_apply,
                    distance,
                    negative,
                    positive,
                    clear,
                    return_zero,
                    home,
                    stall_release,
                ),
            }
        return group

    def _scan_input(
        self,
        layout,
        row,
        column,
        label,
        control,
        object_name,
    ):
        layout.addWidget(QtWidgets.QLabel(label), row, column * 2)
        control.setObjectName(object_name)
        layout.addWidget(control, row, column * 2 + 1)
        self._scan_inputs.append(control)
        return control

    def _build_scan_group(self):
        group = QtWidgets.QGroupBox("扫描运动（连接光谱仪时同步采集并保存）")
        group.setObjectName("motorGroup")
        layout = QtWidgets.QGridLayout(group)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setHorizontalSpacing(7)
        self._scan_inputs = []

        self.scan_x = DirectDoubleSpinBox()
        self.scan_x.setDecimals(4)
        self.scan_x.setRange(1.0 / 320.0, MOTOR_TRAVEL_MM)
        self.scan_x.setValue(10.0)
        self._scan_input(layout, 0, 0, "X 行程 mm", self.scan_x, "scanX")

        self.scan_y = DirectDoubleSpinBox()
        self.scan_y.setDecimals(4)
        self.scan_y.setRange(1.0 / 320.0, MOTOR_TRAVEL_MM)
        self.scan_y.setValue(1.0)
        self._scan_input(layout, 0, 1, "Y 行程 mm", self.scan_y, "scanY")

        self.scan_lines = DirectSpinBox()
        self.scan_lines.setRange(1, 10_000)
        self.scan_lines.setValue(10)
        self._scan_input(
            layout, 1, 0, "行程数 n", self.scan_lines, "scanLineCount"
        )

        self.scan_count = DirectSpinBox()
        self.scan_count.setRange(1, 10_000)
        self.scan_count.setValue(1)
        self._scan_input(
            layout, 1, 1, "扫描次数 m", self.scan_count, "scanCount"
        )

        self.scan_x_steps = DirectSpinBox()
        self.scan_x_steps.setRange(1, 100_000)
        self.scan_x_steps.setValue(1)
        self._scan_input(
            layout, 2, 0, "X 步数", self.scan_x_steps, "scanXSteps"
        )

        self.scan_y_steps = DirectSpinBox()
        self.scan_y_steps.setRange(1, 100_000)
        self.scan_y_steps.setValue(1)
        self._scan_input(
            layout, 2, 1, "Y 步数", self.scan_y_steps, "scanYSteps"
        )

        self.scan_dwell = DirectDoubleSpinBox()
        self.scan_dwell.setDecimals(3)
        self.scan_dwell.setRange(0.0, 3600.0)
        self.scan_dwell.setValue(0.0)
        self.scan_dwell.setSuffix(" s")
        self._scan_input(
            layout, 3, 0, "步时", self.scan_dwell, "scanDwell"
        )

        self.scan_progress = QtWidgets.QLabel("等待开始")
        self.scan_progress.setObjectName("scanProgress")
        layout.addWidget(self.scan_progress, 3, 2, 1, 2)

        note = QtWidgets.QLabel(
            "路径：X 往返蛇形，Y 共走 n 次，X 共走 n+1 次；"
            "扫描不强制机械回零。"
        )
        note.setWordWrap(True)
        note.setObjectName("motorHint")
        layout.addWidget(note, 4, 0, 1, 4)

        button_row = QtWidgets.QHBoxLayout()
        self.scan_start_button = QtWidgets.QPushButton("开始扫描")
        self.scan_start_button.setObjectName("scanStartButton")
        self.scan_start_button.clicked.connect(
            lambda: self.scan_start_requested.emit(self.scan_parameters())
        )
        button_row.addWidget(self.scan_start_button, 2)
        self.scan_stop_button = QtWidgets.QPushButton("停止扫描")
        self.scan_stop_button.setObjectName("scanStopButton")
        self.scan_stop_button.clicked.connect(self.scan_stop_requested)
        button_row.addWidget(self.scan_stop_button, 1)
        layout.addLayout(button_row, 5, 0, 1, 4)
        return group

    def scan_parameters(self) -> ScanParameters:
        return ScanParameters(
            x_mm=self.scan_x.value(),
            y_mm=self.scan_y.value(),
            line_count=self.scan_lines.value(),
            scan_count=self.scan_count.value(),
            x_steps=self.scan_x_steps.value(),
            y_steps=self.scan_y_steps.value(),
            dwell_seconds=self.scan_dwell.value(),
        )

    def set_candidates(self, candidates):
        candidates = tuple(candidates or ())
        if candidates:
            names = "、".join(candidate.port_name for candidate in candidates)
            self.port_label.setText(f"候选串口：{names}")
        elif not self._connected:
            self.port_label.setText("串口：未发现候选设备")

    def set_connection_state(self, connected: bool, detail: str = ""):
        self._connected = bool(connected)
        self.connection_indicator.setText("已连接" if connected else "未连接")
        self.connection_indicator.setProperty(
            "state", "online" if connected else "offline"
        )
        self.connection_indicator.style().unpolish(self.connection_indicator)
        self.connection_indicator.style().polish(self.connection_indicator)
        if detail:
            self.port_label.setText(
                f"串口：{detail}" if connected else str(detail)
            )
        self._refresh_enabled_state()

    def set_motion_active(self, active: bool):
        active = bool(active)
        if active and not self._motion_active:
            focus = QtWidgets.QApplication.focusWidget()
            if isinstance(focus, QtWidgets.QPushButton) and any(
                focus is control
                for controls in self._axis_controls.values()
                for control in controls["interactive"]
            ):
                focus.clearFocus()
        self._motion_active = active
        self._refresh_enabled_state()

    def axis_speed(self, axis) -> int:
        return int(self._axis_controls[Axis(axis)]["speed"].value())

    def set_axis_speed(self, axis, speed_pps, *, device_refresh=False):
        axis = Axis(axis)
        value = int(speed_pps)
        if device_refresh:
            value = abs(value)
        control = self._axis_controls[axis]["speed"]
        control.blockSignals(True)
        control.setValue(value)
        control.blockSignals(False)

    def set_motor_status(self, status: MotorStatus):
        self._safety_locked = bool(status.fault_latched)
        for axis, axis_status in (
            (Axis.X, status.x),
            (Axis.Y, status.y),
        ):
            coordinate = self._axis_controls[axis]["coordinate"]
            coordinate.setText(
                (f"{axis_status.position_mm:.4f} mm "
                 + ("已校准" if axis_status.calibrated else "未校准"))
                if axis_status.position_mm is not None else "坐标不可用"
            )
            limit = self._axis_controls[axis]["limit"]
            if axis_status.zero_limit_active is None:
                limit.setText("限位状态未知")
                limit.setProperty("active", False)
            else:
                limit.setText(
                    "驱动器上报：限位闭合"
                    if axis_status.zero_limit_active
                    else "驱动器上报：限位断开"
                )
                limit.setProperty("active", axis_status.zero_limit_active)
            limit.style().unpolish(limit)
            limit.style().polish(limit)
        self.set_motion_active(status.moving)

    def set_scan_state(self, state: ScanState):
        state = ScanState(state)
        self._scan_active = state not in (
            ScanState.IDLE,
            ScanState.COMPLETED,
            ScanState.FAULTED,
        )
        labels = {
            ScanState.IDLE: "等待开始",
            ScanState.PRECHECK: "正在检查参数",
            ScanState.STARTING_ACQUISITION: "正在启动光谱仪",
            ScanState.SCANNING: (
                "扫描采集中"
                if self._scan_acquisition_enabled
                else "电机扫描中"
            ),
            ScanState.DWELLING: "步进等待",
            ScanState.STOPPING_ACQUISITION: "正在停止并保存",
            ScanState.RETURNING: "正在返回扫描起点",
            ScanState.EXPORTING: "运动完成，正在导出光谱",
            ScanState.STOPPING: "正在安全停止",
            ScanState.COMPLETED: (
                "扫描完成"
                if self._scan_acquisition_enabled
                else "纯电机扫描完成"
            ),
            ScanState.FAULTED: "扫描故障",
        }
        self.scan_progress.setText(labels[state])
        self._refresh_enabled_state()

    def set_scan_acquisition_enabled(self, enabled: bool):
        self._scan_acquisition_enabled = bool(enabled)

    def set_scan_progress(
        self,
        round_number: int,
        round_total: int,
        move_number: int,
        move_total: int,
    ):
        self.scan_progress.setText(
            f"第 {round_number}/{round_total} 轮，动作 "
            f"{move_number}/{move_total}"
        )

    def _refresh_enabled_state(self):
        manual_enabled = (
            self._connected
            and not self._motion_active
            and not self._scan_active
            and not self._safety_locked
        )
        for controls in self._axis_controls.values():
            for control in controls["interactive"]:
                control.setEnabled(manual_enabled)
        self.connect_button.setEnabled(not self._connected and not self._scan_active)
        self.discover_button.setEnabled(not self._scan_active)
        self.disconnect_button.setEnabled(self._connected and not self._scan_active)
        self.stop_button.setEnabled(
            self._connected
            and (self._motion_active or self._scan_active or self._safety_locked)
        )
        self.clear_fault_button.setEnabled(
            self._connected and not self._motion_active and not self._scan_active
        )
        self.settings_button.setEnabled(not self._motion_active and not self._scan_active)
        for control in self._scan_inputs:
            control.setEnabled(not self._scan_active)
        self.scan_start_button.setEnabled(
            self._connected
            and not self._motion_active
            and not self._scan_active
            and not self._safety_locked
        )
        self.scan_stop_button.setEnabled(self._scan_active)
