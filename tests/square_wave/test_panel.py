import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectrometer.qt import QtWidgets
from spectrometer.square_wave.controller import SquareWaveControllerState
from spectrometer.square_wave.models import (
    DeviceIdentity,
    OutputOwner,
    OutputState,
    SquareWaveParameters,
)
from spectrometer.ui.square_wave_panel import SquareWavePanel
from spectrometer.ui.motor_panel import MotorPanel


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = (
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    )
    return _APPLICATION


def state(
    *,
    connected=False,
    output=OutputState.STOPPED,
    owner=OutputOwner.NONE,
    busy=False,
):
    return SquareWaveControllerState(
        connected=connected,
        port_name="/dev/ttyACM0" if connected else "",
        identity=(
            DeviceIdentity("ZGCAI_SQUARE_WAVE", 1) if connected else None
        ),
        output_state=output,
        owner=owner,
        parameters=SquareWaveParameters(10, 5),
        busy=busy,
    )


def test_panel_is_compact_has_required_controls_and_link_defaults_off():
    application()
    panel = SquareWavePanel()

    assert panel.link_checkbox.text() == "扫描联动方波"
    assert not panel.link_checkbox.isChecked()
    assert panel.frequency.value() == 10
    assert panel.frequency.minimum() == 1
    assert panel.frequency.maximum() == 10
    assert panel.pulse_width.value() == 5
    assert panel.pulse_width.minimum() == 1
    assert panel.pulse_width.maximum() == 9999
    assert panel.maximumHeight() <= 70
    assert not any(
        isinstance(child, QtWidgets.QAbstractScrollArea)
        for child in panel.findChildren(QtWidgets.QWidget)
    )


def test_panel_emits_parameters_and_manual_actions():
    application()
    panel = SquareWavePanel()
    panel.set_controller_state(state(connected=True))
    applied = []
    starts = []
    stops = []
    panel.apply_requested.connect(applied.append)
    panel.start_requested.connect(lambda: starts.append(True))
    panel.stop_requested.connect(lambda: stops.append(True))

    panel.frequency.setValue(7)
    panel.pulse_width.setValue(25)
    panel.apply_button.click()
    panel.start_button.click()
    panel.set_controller_state(
        state(
            connected=True,
            output=OutputState.RUNNING,
            owner=OutputOwner.MANUAL,
        )
    )
    panel.stop_button.click()

    assert applied == [SquareWaveParameters(7, 25)]
    assert starts == [True]
    assert stops == [True]


def test_panel_maps_stopped_running_unknown_and_scan_lock_to_unique_controls():
    application()
    panel = SquareWavePanel()

    panel.set_controller_state(state(connected=True))
    assert panel.apply_button.isEnabled()
    assert panel.start_button.isEnabled()
    assert not panel.stop_button.isEnabled()

    panel.set_controller_state(
        state(
            connected=True,
            output=OutputState.RUNNING,
            owner=OutputOwner.MANUAL,
        )
    )
    assert not panel.apply_button.isEnabled()
    assert not panel.start_button.isEnabled()
    assert panel.stop_button.isEnabled()
    assert "输出中" in panel.status_label.text()

    panel.set_controller_state(
        state(connected=True, output=OutputState.UNKNOWN)
    )
    assert not panel.start_button.isEnabled()
    assert panel.stop_button.isEnabled()
    assert "未知" in panel.status_label.text()

    panel.set_scan_active(True)
    assert not panel.connect_button.isEnabled()
    assert not panel.apply_button.isEnabled()
    assert not panel.start_button.isEnabled()
    assert not panel.stop_button.isEnabled()
    assert panel.link_checkbox.isEnabled() is False


def test_linkage_is_session_only_and_new_panel_never_restores_true():
    application()
    first = SquareWavePanel()
    first.link_checkbox.setChecked(True)
    assert first.scan_link_enabled

    second = SquareWavePanel()
    assert not second.scan_link_enabled


def test_motor_workspace_embeds_one_compact_square_wave_panel():
    application()
    motor_panel = MotorPanel()

    panels = motor_panel.findChildren(SquareWavePanel)
    assert panels == [motor_panel.square_wave_panel]
    assert motor_panel.layout().indexOf(motor_panel.square_wave_panel) >= 0
