import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectrometer.motor.lk_md2202 import (
    AxisConfiguration,
    DeviceConfiguration,
    RunCurrent,
)
from spectrometer.motor.settings_store import HostMotorSettings
from spectrometer.qt import QtWidgets
from spectrometer.ui.motor_settings_dialog import (
    MotorSettingsDialog,
    configuration_differences,
)


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return _APPLICATION


def test_dialog_recommended_defaults_match_confirmed_image_and_mechanics():
    application()
    dialog = MotorSettingsDialog(HostMotorSettings(), DeviceConfiguration())
    host, config = dialog.values()
    for axis in (config.x, config.y):
        assert axis.microstep.divisor == 8
        assert axis.run_current is RunCurrent.P50
        assert axis.end_position_pulses == 4800
        assert axis.acceleration == 10_000
        assert axis.deceleration == 10_000
        assert axis.position_speed_pps == 10_000
        assert axis.matches_confirmed_mechanics
    assert host.reverse_x is False
    assert host.reverse_y is False


def test_connected_values_are_displayed_without_automatic_default_override():
    application()
    actual = DeviceConfiguration(
        x=AxisConfiguration(run_current=RunCurrent.P25),
        y=AxisConfiguration(run_current=RunCurrent.P75),
    )
    dialog = MotorSettingsDialog(HostMotorSettings(reverse_x=True), actual)
    host, staged = dialog.values()
    assert staged == actual
    assert host.reverse_x is True


def test_restore_defaults_only_changes_form_and_does_not_emit_apply():
    application()
    actual = DeviceConfiguration(x=AxisConfiguration(run_current=RunCurrent.P25))
    dialog = MotorSettingsDialog(HostMotorSettings(reverse_x=True), actual)
    emitted = []
    dialog.apply_requested.connect(lambda *values: emitted.append(values))
    dialog.restore_recommended_defaults()
    host, staged = dialog.values()
    assert staged == DeviceConfiguration()
    assert host.reverse_x is False
    assert emitted == []


def test_limit_and_mechanical_conversion_controls_are_locked():
    application()
    dialog = MotorSettingsDialog(HostMotorSettings(), DeviceConfiguration())
    for axis in ("x", "y"):
        assert not dialog._axis_controls[(axis, "limit_mode")].isEnabled()
        assert not dialog._axis_controls[(axis, "step_angle")].isEnabled()
        assert not dialog._axis_controls[(axis, "microstep")].isEnabled()
        assert not dialog._axis_controls[(axis, "end_position_pulses")].isEnabled()


def test_difference_summary_only_lists_changes():
    old_host = HostMotorSettings()
    new_host = HostMotorSettings(reverse_y=True)
    current = DeviceConfiguration()
    desired = DeviceConfiguration(x=AxisConfiguration(run_current=RunCurrent.P25))
    differences = configuration_differences(current, desired, old_host, new_host)
    assert len(differences) == 2
    assert any("X/M1" in value and "运行电流" in value for value in differences)
    assert any("Y 方向反转" in value for value in differences)
