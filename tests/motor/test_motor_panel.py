import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectrometer.motor.models import (
    Axis,
    Direction,
    MotorAxisStatus,
    MotorStatus,
)
from spectrometer.motor.lk_md2202 import AxisConfiguration, DeviceConfiguration
from spectrometer.motor.scan_controller import ScanState
from spectrometer.qt import QtCore, QtWidgets, Signal
from spectrometer.ui.main_window import MainWindow
from spectrometer.ui.motor_panel import MotorPanel


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = (
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    )
    return _APPLICATION


def _status():
    return MotorStatus(
        MotorAxisStatus(Axis.X, 400, 1.25, True, False, False, mechanical_position_mm=1.25),
        MotorAxisStatus(Axis.Y, 0, 0.0, True, False, True, mechanical_position_mm=0.0),
    )


class FakeUiMotor(QtCore.QObject):
    candidates_changed = Signal(object)
    connection_changed = Signal(bool, str)
    status_changed = Signal(object)
    operation_failed = Signal(str)
    diagnostic_event = Signal(str)
    motion_started = Signal(str)
    motion_finished = Signal(str, bool, str)
    configuration_changed = Signal(object)
    speed_applied = Signal(object, int, int, str)

    def __init__(self):
        super().__init__()
        self.connected = True
        self.device_id = "MOTOR-UI"
        self.motion_active = False
        self.status = MotorStatus(
            MotorAxisStatus(Axis.X, 0, 0.0, True, False, True, mechanical_position_mm=0.0),
            MotorAxisStatus(Axis.Y, 0, 0.0, True, False, True, mechanical_position_mm=0.0),
        )
        self.moves = []
        self.releases = []
        self.safety_locked = False
        self.configuration = None

    def discover(self, _excluded_ports=()):
        return ()

    def connect_auto(self, _excluded_ports=()):
        return True

    def disconnect(self):
        self.connected = False

    def set_speed(self, *_args):
        return True

    def set_position(self, *_args):
        return True

    def return_axis_to_zero(self, *_args):
        return None

    def home(self, *_args):
        return None

    def release_stall(self, axis, speed):
        self.releases.append((Axis(axis), int(speed)))
        return "release-1"

    def clear_faults(self):
        return True

    def move_relative(self, axis, distance, direction):
        operation_id = f"move-{len(self.moves) + 1}"
        self.moves.append((Axis(axis), float(distance), Direction(direction)))
        self.motion_active = True
        self.motion_started.emit(operation_id)
        return operation_id

    def stop(self):
        self.motion_active = False

    def shutdown(self):
        self.stop()


def test_motor_panel_defaults_match_confirmed_scan_contract():
    application()
    panel = MotorPanel()

    parameters = panel.scan_parameters()
    assert parameters.x_mm == 10.0
    assert parameters.y_mm == 1.0
    assert parameters.line_count == 10
    assert parameters.scan_count == 1
    assert parameters.x_steps == 1
    assert parameters.y_steps == 1
    assert parameters.dwell_seconds == 0.0
    assert not panel.scan_start_button.isEnabled()
    assert not panel.scan_stop_button.isEnabled()
    assert sum(
        button.text() == "机械回零"
        for button in panel.findChildren(QtWidgets.QPushButton)
    ) == 2
    assert sum(
        button.text() == "脱离卡死"
        for button in panel.findChildren(QtWidgets.QPushButton)
    ) == 2


def test_motor_panel_emits_manual_and_scan_requests():
    application()
    panel = MotorPanel()
    panel.set_connection_state(True, "/dev/ttyACM0")
    moves = []
    scans = []
    panel.move_requested.connect(
        lambda axis, distance, direction:
        moves.append((axis, distance, direction))
    )
    panel.scan_start_requested.connect(scans.append)

    panel.findChild(QtWidgets.QDoubleSpinBox, "motorXDistance").setValue(2.5)
    plus = next(
        button
        for button in panel.findChildren(QtWidgets.QPushButton)
        if button.toolTip() == "X 轴正向运动"
    )
    plus.click()
    panel.scan_start_button.click()

    assert moves == [(Axis.X, 2.5, Direction.POSITIVE)]
    assert scans == [panel.scan_parameters()]


def test_motion_start_does_not_move_button_focus_to_scan_x():
    app = application()
    panel = MotorPanel()
    panel.set_connection_state(True, "/dev/ttyUSB0")
    panel.show()
    plus = next(
        button
        for button in panel.findChildren(QtWidgets.QPushButton)
        if button.toolTip() == "X 轴正向运动"
    )
    plus.setFocus()
    app.processEvents()
    assert plus.hasFocus()

    panel.set_motion_active(True)
    app.processEvents()

    assert not panel.scan_x.hasFocus()
    assert panel.scan_x.lineEdit().selectedText() == ""
    panel.close()
    app.processEvents()


def test_motor_panel_emits_signed_speed_for_setting_and_stall_release():
    application()
    panel = MotorPanel()
    panel.set_connection_state(True, "/dev/ttyUSB0")
    speeds = []
    releases = []
    panel.speed_requested.connect(lambda axis, value: speeds.append((axis, value)))
    panel.stall_release_requested.connect(
        lambda axis, value: releases.append((axis, value))
    )
    speed = panel.findChild(QtWidgets.QSpinBox, "motorXSpeed")
    speed.setValue(-5000)
    panel._axis_controls[Axis.X]["speed_apply"].click()
    panel._axis_controls[Axis.X]["stall_release"].click()
    assert speeds == [(Axis.X, -5000)]
    assert releases == [(Axis.X, -5000)]


def test_motor_panel_preserves_session_sign_but_can_reset_to_device_speed():
    application()
    panel = MotorPanel()
    panel.set_axis_speed(Axis.X, -5000)
    assert panel.axis_speed(Axis.X) == -5000
    panel.set_axis_speed(Axis.X, 10_000, device_refresh=True)
    assert panel.axis_speed(Axis.X) == 10_000


def test_motor_panel_displays_coordinates_limits_and_scan_lock():
    application()
    panel = MotorPanel()
    panel.set_connection_state(True, "/dev/ttyACM0")
    panel.set_motor_status(_status())

    assert panel.findChild(
        QtWidgets.QLabel, "motorXCoordinate"
    ).text() == "1.2500 mm 已校准"
    assert panel.findChild(
        QtWidgets.QLabel, "motorYCoordinate"
    ).text() == "0.0000 mm 已校准"
    y_limit = panel._axis_controls[Axis.Y]["limit"]
    assert y_limit.property("active") is True
    assert y_limit.text() == "驱动器上报：限位闭合"

    unknown = MotorStatus(
        MotorAxisStatus(Axis.X, 0, 0.0, False, False, None),
        MotorAxisStatus(Axis.Y, 0, 0.0, False, False, False),
    )
    panel.set_motor_status(unknown)
    assert panel._axis_controls[Axis.X]["limit"].text() == "限位状态未知"
    assert panel._axis_controls[Axis.Y]["limit"].text() == "驱动器上报：限位断开"

    panel.set_scan_state(ScanState.SCANNING)
    assert not panel.scan_x.isEnabled()
    assert not panel.scan_start_button.isEnabled()
    assert panel.scan_stop_button.isEnabled()


def test_motor_panel_locks_motor_and_scan_when_stop_is_unconfirmed():
    application()
    panel = MotorPanel()
    panel.set_connection_state(True, "/dev/ttyUSB0")
    locked = MotorStatus(
        MotorAxisStatus(Axis.X, 0, 0.0, False, False, False),
        MotorAxisStatus(Axis.Y, 0, 0.0, False, False, False),
        fault_latched=True,
        fault_reason="stop_unconfirmed",
    )
    panel.set_motor_status(locked)
    assert not panel.scan_start_button.isEnabled()
    assert not panel._axis_controls[Axis.X]["stall_release"].isEnabled()
    assert panel.stop_button.isEnabled()


def test_live_workspace_embeds_motor_panel_below_plot(tmp_path):
    app = application()
    window = MainWindow(
        simulation=True,
        auto_start_simulation=False,
        settings_path=tmp_path / "settings.json",
    )

    assert window.windowTitle() == "ZGCAI 光谱仪采集与分析工作站"
    assert window.live_workspace.orientation() == QtCore.Qt.Vertical
    assert window.live_workspace.widget(0) is window.plot_widget
    assert window.live_workspace.widget(1) is window.motor_panel
    assert window.tabs.widget(0) is window.live_workspace
    assert "激光" not in window.motor_panel.findChild(
        QtWidgets.QLabel, "motorPanelTitle"
    ).text()

    window.close()
    app.processEvents()


def test_main_window_finishes_motor_discovery_status_and_resets_device_speed_sign(tmp_path):
    app = application()
    motor = FakeUiMotor()
    window = MainWindow(
        simulation=True,
        auto_start_simulation=False,
        settings_path=tmp_path / "settings.json",
        motor_controller=motor,
    )
    window.status_panel.state_label.setText("正在识别电机串口……")
    window._motor_connection_changed(True, "ttyUSB0")
    assert window.status_panel.state_label.text() == "电机已连接：ttyUSB0"

    window.motor_panel.set_axis_speed(Axis.X, -5000)
    motor.configuration_changed.emit(
        DeviceConfiguration(x=AxisConfiguration(position_speed_pps=10_000))
    )
    assert window.motor_panel.axis_speed(Axis.X) == 10_000
    motor.speed_applied.emit(
        Axis.X,
        -5000,
        5000,
        "运动速度已设为 5000 pps；脱离卡死速度为 -5000 pps",
    )
    assert window.motor_panel.axis_speed(Axis.X) == -5000

    window._motor_connection_changed(False, "未找到LK-MD2202电机驱动板")
    assert window.status_panel.state_label.text() == "未找到LK-MD2202电机驱动板"
    window._motor_connection_changed(False, "")
    assert window.status_panel.state_label.text() == "电机已断开"
    window.close()
    app.processEvents()


def test_motor_stop_unknown_does_not_disable_independent_spectrometer_controls(tmp_path):
    app = application()
    motor = FakeUiMotor()
    window = MainWindow(
        simulation=True,
        auto_start_simulation=False,
        settings_path=tmp_path / "settings.json",
        motor_controller=motor,
    )
    acquisition_was_enabled = window.ribbon.acquisition_button.isEnabled()
    locked = MotorStatus(
        MotorAxisStatus(Axis.X, 0, 0.0, False, False, False),
        MotorAxisStatus(Axis.Y, 0, 0.0, False, False, False),
        fault_latched=True,
        fault_reason="stop_unconfirmed",
    )
    motor.safety_locked = True
    motor.status_changed.emit(locked)
    assert not window.motor_panel.scan_start_button.isEnabled()
    assert window.status_panel.state_label.text() == "stop_unconfirmed"
    assert window.ribbon.acquisition_button.isEnabled() is acquisition_was_enabled
    window.close()
    app.processEvents()


def test_main_window_starts_scan_as_scan_owned_global_acquisition(tmp_path):
    app = application()
    motor = FakeUiMotor()
    window = MainWindow(
        simulation=True,
        auto_start_simulation=False,
        settings_path=tmp_path / "settings.json",
        motor_controller=motor,
    )
    window.motor_panel.set_connection_state(True, "MOTOR-UI")

    assert window._start_motor_scan(
        window.motor_panel.scan_parameters()
    )
    task = window.control.active_task_for_device(0)
    assert task is not None
    assert task.owner.value == "scan"
    assert window.scan_controller.state is ScanState.SCANNING
    assert motor.moves
    assert not window.ribbon.acquisition_button.isEnabled()

    window.close()
    app.processEvents()
