"""Compact square-wave controls embedded in the motor workspace."""

from __future__ import annotations

from ..qt import QtWidgets, Signal
from ..square_wave.controller import SquareWaveControllerState
from ..square_wave.models import (
    MAX_FREQUENCY_HZ,
    MAX_PULSE_WIDTH_US,
    MIN_FREQUENCY_HZ,
    MIN_PULSE_WIDTH_US,
    OutputOwner,
    OutputState,
    SquareWaveParameters,
)
from .input_controls import DirectSpinBox


class SquareWavePanel(QtWidgets.QWidget):
    discover_requested = Signal()
    connect_requested = Signal()
    disconnect_requested = Signal()
    apply_requested = Signal(object)
    start_requested = Signal()
    stop_requested = Signal()
    settings_requested = Signal()
    link_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("squareWavePanel")
        self.setMaximumHeight(62)
        self._state = None
        self._scan_active = False

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(7, 3, 7, 3)
        row.setSpacing(5)

        title = QtWidgets.QLabel("方波")
        title.setObjectName("squareWaveTitle")
        row.addWidget(title)

        self.status_label = QtWidgets.QLabel("未连接")
        self.status_label.setObjectName("squareWaveStatus")
        self.status_label.setMinimumWidth(92)
        row.addWidget(self.status_label)

        row.addWidget(QtWidgets.QLabel("频率 Hz"))
        self.frequency = DirectSpinBox()
        self.frequency.setObjectName("squareWaveFrequency")
        self.frequency.setRange(MIN_FREQUENCY_HZ, MAX_FREQUENCY_HZ)
        self.frequency.setValue(SquareWaveParameters().frequency_hz)
        self.frequency.setMaximumWidth(72)
        row.addWidget(self.frequency)

        row.addWidget(QtWidgets.QLabel("脉宽 μs"))
        self.pulse_width = DirectSpinBox()
        self.pulse_width.setObjectName("squareWavePulseWidth")
        self.pulse_width.setRange(MIN_PULSE_WIDTH_US, MAX_PULSE_WIDTH_US)
        self.pulse_width.setValue(SquareWaveParameters().pulse_width_us)
        self.pulse_width.setMaximumWidth(90)
        row.addWidget(self.pulse_width)

        self.connect_button = QtWidgets.QPushButton("自动连接")
        self.connect_button.setObjectName("squareWaveConnectButton")
        self.connect_button.clicked.connect(self.connect_requested)
        row.addWidget(self.connect_button)

        self.disconnect_button = QtWidgets.QPushButton("断开")
        self.disconnect_button.clicked.connect(self.disconnect_requested)
        row.addWidget(self.disconnect_button)

        self.apply_button = QtWidgets.QPushButton("应用")
        self.apply_button.clicked.connect(self._request_apply)
        row.addWidget(self.apply_button)

        self.start_button = QtWidgets.QPushButton("启动")
        self.start_button.clicked.connect(self.start_requested)
        row.addWidget(self.start_button)

        self.stop_button = QtWidgets.QPushButton("停止")
        self.stop_button.clicked.connect(self.stop_requested)
        row.addWidget(self.stop_button)

        self.link_checkbox = QtWidgets.QCheckBox("扫描联动方波")
        self.link_checkbox.setObjectName("squareWaveScanLink")
        self.link_checkbox.setChecked(False)
        self.link_checkbox.toggled.connect(self.link_changed)
        row.addWidget(self.link_checkbox)

        self.settings_button = QtWidgets.QPushButton("方波设置")
        self.settings_button.clicked.connect(self.settings_requested)
        row.addWidget(self.settings_button)
        row.addStretch(1)
        self._refresh_enabled_state()

    @property
    def scan_link_enabled(self):
        return bool(self.link_checkbox.isChecked())

    def parameters(self):
        return SquareWaveParameters(
            int(self.frequency.value()),
            int(self.pulse_width.value()),
        )

    def _request_apply(self):
        self.apply_requested.emit(self.parameters())

    def set_controller_state(self, state):
        if not isinstance(state, SquareWaveControllerState):
            return
        self._state = state
        for control, value in (
            (self.frequency, state.parameters.frequency_hz),
            (self.pulse_width, state.parameters.pulse_width_us),
        ):
            control.blockSignals(True)
            control.setValue(int(value))
            control.blockSignals(False)
        if not state.connected:
            text = "未连接"
        elif state.output_state is OutputState.UNKNOWN:
            text = "输出状态未知"
        elif state.output_state is OutputState.RUNNING:
            text = (
                "扫描输出中"
                if state.owner is OutputOwner.SCAN
                else "手动输出中"
            )
        elif state.busy:
            text = "处理中"
        else:
            text = "已连接/已停止"
        self.status_label.setText(text)
        self.status_label.setToolTip(state.detail or state.port_name)
        self._refresh_enabled_state()

    def set_scan_active(self, active):
        self._scan_active = bool(active)
        self._refresh_enabled_state()

    def set_candidates(self, candidates):
        candidates = tuple(candidates or ())
        if candidates and not (self._state and self._state.connected):
            self.status_label.setText(f"候选 {len(candidates)} 个")

    def _refresh_enabled_state(self):
        state = self._state
        connected = bool(state and state.connected)
        busy = bool(state and state.busy)
        stopped = bool(
            state and state.output_state is OutputState.STOPPED
        )
        running_or_unknown = bool(
            state
            and state.output_state
            in {OutputState.RUNNING, OutputState.UNKNOWN}
        )
        owner = state.owner if state else OutputOwner.NONE
        idle = not self._scan_active and not busy
        editable = connected and idle and stopped and owner is OutputOwner.NONE

        self.frequency.setEnabled(editable)
        self.pulse_width.setEnabled(editable)
        self.apply_button.setEnabled(editable)
        self.start_button.setEnabled(editable)
        self.stop_button.setEnabled(
            connected
            and idle
            and running_or_unknown
            and owner is not OutputOwner.SCAN
        )
        self.connect_button.setEnabled(not connected and idle)
        self.disconnect_button.setEnabled(connected and idle)
        self.settings_button.setEnabled(idle and not running_or_unknown)
        self.link_checkbox.setEnabled(not self._scan_active)
