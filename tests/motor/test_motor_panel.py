import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectrometer.motor.models import (
    Axis,
    Direction,
    MotorAxisStatus,
    MotorStatus,
)
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

    panel.set_scan_state(ScanState.SCANNING)
    assert not panel.scan_x.isEnabled()
    assert not panel.scan_start_button.isEnabled()
    assert panel.scan_stop_button.isEnabled()


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
