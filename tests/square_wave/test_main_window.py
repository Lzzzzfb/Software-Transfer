import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectrometer.qt import QtCore, QtWidgets, Signal
from spectrometer.square_wave.controller import SquareWaveControllerState
from spectrometer.square_wave.models import (
    OutputOwner,
    OutputState,
    SquareWaveParameters,
)
from spectrometer.square_wave.settings_store import HostSquareWaveSettings
from spectrometer.ui.main_window import MainWindow


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = (
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    )
    return _APPLICATION


class FakeSquareWaveController(QtCore.QObject):
    candidates_changed = Signal(object)
    connection_changed = Signal(bool, str)
    status_changed = Signal(object)
    parameters_applied = Signal(bool, str)
    operation_failed = Signal(str)
    diagnostic_event = Signal(str)
    scan_round_prepared = Signal(bool, str)
    scan_round_finished = Signal(bool, str)

    def __init__(self, *, connected=False):
        super().__init__()
        self.connected = bool(connected)
        self.port_name = "/dev/ttyACM0" if connected else ""
        self.identity = None
        self.serial_number = ""
        self.candidates = ()
        self.host_settings = HostSquareWaveSettings()
        self.connect_auto_calls = []
        self.shutdown_called = False
        self._state = SquareWaveControllerState(
            connected=self.connected,
            port_name=self.port_name,
            identity=None,
            output_state=OutputState.STOPPED,
            owner=OutputOwner.NONE,
            parameters=SquareWaveParameters(),
            busy=False,
        )

    @property
    def state(self):
        return self._state

    def discover(self, _excluded=()):
        return ()

    def connect_auto(self, excluded=()):
        self.connect_auto_calls.append(set(excluded))
        return True

    def connect_manual(self, _port):
        return True

    def disconnect(self):
        return True

    def apply_parameters(self, _parameters):
        return True

    def start_output(self):
        return True

    def stop_output(self):
        return True

    def update_host_settings(self, settings):
        self.host_settings = settings
        return True

    def shutdown(self):
        self.shutdown_called = True


def test_main_window_wires_square_wave_controls_and_resets_link_off(tmp_path):
    app = application()
    square_wave = FakeSquareWaveController()
    window = MainWindow(
        simulation=True,
        auto_start_simulation=False,
        settings_path=tmp_path / "settings.json",
        square_wave_controller=square_wave,
    )

    panel = window.motor_panel.square_wave_panel
    assert not panel.scan_link_enabled
    panel.connect_button.click()
    assert len(square_wave.connect_auto_calls) == 1
    window.close()
    app.processEvents()
    assert square_wave.shutdown_called


def test_connected_square_wave_port_is_excluded_from_motor_discovery(tmp_path):
    app = application()
    square_wave = FakeSquareWaveController(connected=True)
    window = MainWindow(
        simulation=True,
        auto_start_simulation=False,
        settings_path=tmp_path / "settings.json",
        square_wave_controller=square_wave,
    )

    assert "/dev/ttyACM0" in window._motor_excluded_ports()
    window.close()
    app.processEvents()
