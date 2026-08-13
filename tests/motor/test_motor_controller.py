import os
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectrometer.motor.controller import MotorController
from spectrometer.motor.lk_md2202 import (
    AxisConfiguration,
    AxisDeviceStatus,
    CommunicationConfiguration,
    DeviceConfiguration,
    DriverAxis,
    LEGACY_IDENTITY_REGISTERS,
    RunCurrent,
    relative_move_request,
    position_speed_request,
    stop_request,
    velocity_mode_request,
)
from spectrometer.motor.models import Axis, Direction
from spectrometer.motor.settings_store import MotorSettingsStore
from spectrometer.qt import QtCore, QtWidgets, Signal


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return _APPLICATION


class FakeTransport(QtCore.QObject):
    connection_changed = Signal(bool, str)
    command_completed = Signal(object, object)
    command_failed = Signal(object, str)
    diagnostic_event = Signal(str)

    def __init__(self):
        super().__init__()
        self.connected = False
        self.port_name = ""
        self.sent = []
        self.emergency = []
        self.opened = []

    @property
    def busy(self):
        return False

    def connect_port(self, port_name, baud_rate=9600):
        self.connected = True
        self.port_name = port_name
        self.opened.append((port_name, baud_rate))
        self.connection_changed.emit(True, "")

    def disconnect_port(self):
        self.connected = False

    def send_request(self, request, **options):
        self.sent.append((bytes(request), options))
        if not self.connected:
            self.command_failed.emit(options.get("tag"), "not_connected")
            return False
        return True

    def send_emergency(self, requests, **_options):
        self.emergency.append(tuple(bytes(request) for request in requests))

    def shutdown(self):
        self.disconnect_port()

    def complete_last(self, registers):
        _request, options = self.sent[-1]
        self.command_completed.emit(
            options.get("tag"), SimpleNamespace(registers=tuple(registers))
        )


PORTS = [{
    "port_name": "COM8",
    "system_location": "COM8",
    "serial_number": "RS485-A",
    "vendor_id": 0x0403,
    "product_id": 0x6001,
}]


def identity_registers(name="LK-MD2202"):
    raw = name.encode("ascii").ljust(20, b"\0")
    name_regs = [int.from_bytes(raw[index:index + 2], "big") for index in range(0, 20, 2)]
    return [0x0100, 0, 0, 0, 0, 0, *name_regs]


def axis_config_registers(config=AxisConfiguration()):
    end_hi, end_lo = divmod(config.end_position_pulses, 0x10000)
    speed_hi, speed_lo = divmod(config.position_speed_pps, 0x10000)
    return [
        int(config.step_angle), int(config.microstep), int(config.run_current),
        int(config.limit_mode), end_hi, end_lo, 0, 0, 0,
        int(config.stop_current), config.acceleration, config.deceleration,
        0, 0, speed_hi, speed_lo,
    ]


def connect_controller(tmp_path, identity_response=None):
    application()
    transport = FakeTransport()
    controller = MotorController(
        transport=transport,
        settings_store=MotorSettingsStore(tmp_path / "motor-settings.json"),
        port_provider=lambda: PORTS,
    )
    assert controller.connect_auto()
    assert transport.sent[-1][1]["tag"] == ("probe",)
    transport.complete_last(identity_response or identity_registers())
    assert transport.sent[-1][1]["tag"] == ("init", "x_config")
    transport.complete_last(axis_config_registers())
    transport.complete_last(axis_config_registers())
    transport.complete_last([1, 3, 0, 0, 0])
    transport.complete_last([0, 0, 0, 0])
    transport.complete_last([0, 0, 0, 0])
    assert controller.connected
    return controller, transport


def test_auto_connect_uses_read_only_identity_then_reads_configuration(tmp_path):
    controller, transport = connect_controller(tmp_path)
    assert controller.device_id == "RS485-A"
    assert all(item[0][1] == 0x03 for item in transport.sent[:6])
    assert controller.configuration == DeviceConfiguration()


def test_auto_connect_accepts_exact_legacy_identity_after_full_read_only_init(tmp_path):
    controller, transport = connect_controller(tmp_path, LEGACY_IDENTITY_REGISTERS)
    assert controller.connected
    assert len(transport.sent) == 6
    assert all(request[0][1] == 0x03 for request in transport.sent)


def test_auto_connect_rejects_near_legacy_identity_before_initialization(tmp_path):
    application()
    transport = FakeTransport()
    controller = MotorController(
        transport=transport,
        settings_store=MotorSettingsStore(tmp_path / "motor-settings.json"),
        port_provider=lambda: PORTS,
    )
    failures = []
    controller.operation_failed.connect(failures.append)
    assert controller.connect_auto()
    near_match = list(LEGACY_IDENTITY_REGISTERS)
    near_match[1] ^= 1
    transport.complete_last(near_match)
    assert not controller.connected
    assert len(transport.sent) == 1
    assert failures[-1] == "候选串口均未通过 LK-MD2202 只读身份校验"


def test_relative_move_uses_one_signed_32bit_modbus_action(tmp_path):
    controller, transport = connect_controller(tmp_path)
    operation_id = controller.move_relative(Axis.X, 1.0, Direction.POSITIVE)
    assert operation_id
    request, options = transport.sent[-1]
    assert request == relative_move_request(1, DriverAxis.X, 320)
    assert options["action"] is True
    assert options.get("read_retries", 0) == 0


def test_negative_speed_sets_absolute_position_speed_and_reports_signed_session_value(tmp_path):
    controller, transport = connect_controller(tmp_path)
    applied = []
    controller.speed_applied.connect(
        lambda axis, signed, absolute, message: applied.append(
            (axis, signed, absolute, message)
        )
    )
    assert controller.set_speed(Axis.X, -5000)
    request, options = transport.sent[-1]
    assert request == position_speed_request(1, DriverAxis.X, 5000)
    assert options["tag"] == ("speed", Axis.X, -5000, 5000)
    transport.complete_last(())
    assert controller.configuration.x.position_speed_pps == 5000
    assert applied[-1][:3] == (Axis.X, -5000, 5000)


@pytest.mark.parametrize("speed", [0, 99, -99, 14_001, -14_001])
def test_speed_rejects_zero_and_absolute_values_outside_normal_range(tmp_path, speed):
    controller, transport = connect_controller(tmp_path)
    before = len(transport.sent)
    assert not controller.set_speed(Axis.X, speed)
    assert len(transport.sent) == before


def test_stall_release_from_closed_limit_stops_as_soon_as_limit_opens(tmp_path):
    controller, transport = connect_controller(tmp_path)
    controller._axis_device_status[Axis.X] = AxisDeviceStatus(True, 0, 0)
    controller._refresh_status()
    finished = []
    controller.motion_finished.connect(
        lambda operation_id, success, reason: finished.append(
            (operation_id, success, reason)
        )
    )

    operation_id = controller.release_stall(Axis.X, 5000)
    assert operation_id
    assert transport.sent[-1][0] == velocity_mode_request(1, DriverAxis.X, 5000)
    transport.complete_last(())
    controller._poll_motion()
    transport.complete_last([0, 1, 0, 100])
    assert transport.sent[-1][0] == velocity_mode_request(1, DriverAxis.X, 0)
    transport.complete_last(())
    assert transport.sent[-1][1]["tag"] == ("release_stop_status", operation_id)
    transport.complete_last([0, 0, 0, 100])

    assert finished[-1] == (operation_id, True, "stall_release_limit_released")
    assert not controller.status.x.calibrated


def test_stall_release_from_open_limit_stops_if_wrong_direction_closes_it(tmp_path):
    controller, transport = connect_controller(tmp_path)
    finished = []
    controller.motion_finished.connect(
        lambda operation_id, success, reason: finished.append(
            (operation_id, success, reason)
        )
    )
    operation_id = controller.release_stall(Axis.X, -5000)
    transport.complete_last(())
    controller._poll_motion()
    transport.complete_last([1, 1, 0, 0])
    assert transport.sent[-1][0] == velocity_mode_request(1, DriverAxis.X, 0)
    transport.complete_last(())
    transport.complete_last([1, 0, 0, 0])
    assert finished[-1] == (operation_id, False, "stall_release_wrong_direction")


def test_stall_release_uses_travel_deadline_then_confirms_stop(tmp_path):
    controller, transport = connect_controller(tmp_path)
    finished = []
    controller.motion_finished.connect(
        lambda operation_id, success, reason: finished.append(
            (operation_id, success, reason)
        )
    )
    operation_id = controller.release_stall(Axis.X, 10_000)
    transport.complete_last(())
    controller._active_motion = replace(controller._active_motion, deadline=0)
    controller._poll_motion()
    assert transport.sent[-1][0] == velocity_mode_request(1, DriverAxis.X, 0)
    transport.complete_last(())
    transport.complete_last([0, 0, 0, 0])
    assert finished[-1] == (operation_id, True, "stall_release_timeout")


def test_stall_release_stop_failure_locks_motion_and_clear_faults_only_verifies_status(tmp_path):
    controller, transport = connect_controller(tmp_path)
    controller.release_stall(Axis.X, 5000)
    transport.complete_last(())
    controller._active_motion = replace(controller._active_motion, deadline=0)
    controller._poll_motion()
    tag = transport.sent[-1][1]["tag"]
    transport.command_failed.emit(tag, "result_unknown")
    assert transport.sent[-1][0] == stop_request(1, DriverAxis.X)
    fallback_tag = transport.sent[-1][1]["tag"]
    transport.command_failed.emit(fallback_tag, "result_unknown")
    assert controller.safety_locked
    assert controller.status.fault_latched
    assert not controller.move_relative(Axis.X, 1.0, Direction.POSITIVE)

    assert controller.clear_faults()
    assert controller.safety_locked
    assert transport.sent[-1][1]["tag"] == ("safety_status", Axis.X)
    transport.complete_last([0, 0, 0, 0])
    transport.complete_last([0, 0, 0, 0])
    assert not controller.safety_locked


def test_emergency_lock_clears_only_after_both_axes_report_stopped(tmp_path):
    controller, transport = connect_controller(tmp_path)
    controller._start_emergency_stop("test_stop")
    assert controller.safety_locked
    assert len(transport.emergency[-1]) == 4

    for _ in range(4):
        transport.command_completed.emit(("stop",), SimpleNamespace(registers=()))
    assert transport.sent[-1][1]["tag"] == ("safety_status", Axis.X)
    transport.complete_last([0, 0, 0, 0])
    assert transport.sent[-1][1]["tag"] == ("safety_status", Axis.Y)
    transport.complete_last([0, 0, 0, 0])
    assert not controller.safety_locked


def test_emergency_lock_remains_when_an_axis_still_reports_motion(tmp_path):
    controller, transport = connect_controller(tmp_path)
    controller._start_emergency_stop("test_stop")
    for _ in range(4):
        transport.command_completed.emit(("stop",), SimpleNamespace(registers=()))
    transport.complete_last([0, 0, 0, 0])
    transport.complete_last([0, 1, 0, 0])
    assert controller.safety_locked
    assert controller.status.fault_latched


def test_idle_poll_refreshes_both_limits_and_marks_failed_axis_unknown(tmp_path):
    controller, transport = connect_controller(tmp_path)
    controller._poll_idle_status()
    assert transport.sent[-1][1]["tag"] == ("idle_status", Axis.X)
    transport.complete_last([1, 0, 0, 0])
    assert transport.sent[-1][1]["tag"] == ("idle_status", Axis.Y)
    transport.complete_last([0, 0, 0, 0])
    assert controller.status.x.zero_limit_active is True
    assert controller.status.y.zero_limit_active is False

    controller._poll_idle_status()
    tag = transport.sent[-1][1]["tag"]
    transport.command_failed.emit(tag, "timeout")
    assert controller.status.x.zero_limit_active is None
    assert not controller.safety_locked


def test_software_zero_allows_negative_coordinates_without_power_cycle_restore(tmp_path):
    controller, _transport = connect_controller(tmp_path)
    assert controller.clear_software_zero(Axis.X)
    controller._axis_device_status[Axis.X] = SimpleNamespace(
        position_pulses=-320,
        moving=False,
        limit_active=False,
    )
    controller._refresh_status()
    assert controller.status.x.software_position_mm == -1.0
    assert controller.status.x.calibrated is False


def test_mechanical_home_establishes_calibrated_zero(tmp_path):
    controller, transport = connect_controller(tmp_path)
    operation_id = controller.home(Axis.X)
    assert operation_id
    assert 1.9 <= controller._active_motion.deadline - time.monotonic() <= 5.0
    transport.complete_last(())  # home write acknowledgement
    controller._poll_motion()
    transport.complete_last([1, 1, 0, 20])
    controller._poll_motion()
    transport.complete_last([1, 0, 0, 0])
    assert controller.status.x.calibrated is True
    assert controller.status.x.software_position_mm == 0.0


def test_mechanical_home_rejects_limit_that_is_already_closed(tmp_path):
    controller, transport = connect_controller(tmp_path)
    controller._axis_device_status[Axis.X] = AxisDeviceStatus(True, 0, 0)
    controller._refresh_status()
    before = len(transport.sent)
    assert controller.home(Axis.X) is None
    assert len(transport.sent) == before
    assert not controller.status.x.calibrated


def test_motion_action_timeout_reads_status_instead_of_retrying(tmp_path):
    controller, transport = connect_controller(tmp_path)
    completed = []
    controller.motion_finished.connect(
        lambda operation_id, success, reason: completed.append(
            (operation_id, success, reason)
        )
    )
    operation_id = controller.move_relative(
        Axis.X, 1.0, Direction.POSITIVE
    )
    move_request, options = transport.sent[-1]
    transport.command_failed.emit(options["tag"], "result_unknown")
    assert transport.sent[-1][1]["tag"] == (
        "motion_status",
        operation_id,
    )
    assert transport.sent.count((move_request, options)) == 1
    transport.complete_last([0, 0, 0, 320])
    assert completed[-1] == (operation_id, True, "completed")


def test_home_requires_zero_limit_confirmation(tmp_path):
    controller, transport = connect_controller(tmp_path)
    failures = []
    controller.motion_finished.connect(
        lambda _operation_id, success, reason: failures.append(
            (success, reason)
        )
    )
    controller.home(Axis.X)
    transport.complete_last(())
    controller._poll_motion()
    transport.complete_last([0, 0, 0, 0])
    assert failures[-1] == (False, "home_zero_limit_not_confirmed")
    assert controller.status.x.calibrated is False


def test_configuration_writes_only_changed_fields_then_reads_back(tmp_path):
    controller, transport = connect_controller(tmp_path)
    desired = DeviceConfiguration(
        x=AxisConfiguration(run_current=RunCurrent.P37_5),
        y=AxisConfiguration(),
    )
    before = len(transport.sent)
    assert controller.apply_configuration(desired, controller.host_settings)
    writes = transport.sent[before:]
    assert len(writes) == 1
    assert writes[0][0][1] == 0x06
    assert writes[0][1]["tag"] == ("config_write", "X.run_current")
    transport.complete_last(())
    assert transport.sent[-1][1]["tag"] == ("init", "x_config")


def test_scan_lease_rejects_configuration_write(tmp_path):
    controller, transport = connect_controller(tmp_path)
    controller.set_scan_active(True)
    before = len(transport.sent)
    assert not controller.apply_configuration(DeviceConfiguration(), controller.host_settings)
    assert len(transport.sent) == before


def test_reversed_axis_checks_logical_travel_and_accepts_negative_driver_position(tmp_path):
    controller, transport = connect_controller(tmp_path)
    controller.host_settings = controller.host_settings.__class__(reverse_x=True)
    controller._calibrated[Axis.X] = True
    controller._refresh_status()
    operation_id = controller.move_relative(Axis.X, 1.0, Direction.POSITIVE)
    assert operation_id
    assert transport.sent[-1][0] == relative_move_request(1, DriverAxis.X, -320)

    controller._active_motion = None
    controller._axis_device_status[Axis.X] = SimpleNamespace(
        position_pulses=-320,
        moving=False,
        limit_active=False,
    )
    controller._refresh_status()
    assert controller.status.x.mechanical_position_mm == 1.0


def test_address_and_baud_changes_reconnect_between_each_write(tmp_path):
    controller, transport = connect_controller(tmp_path)
    desired = DeviceConfiguration(
        communication=CommunicationConfiguration(address=7, baud_rate=19200)
    )
    assert controller.apply_configuration(desired, controller.host_settings)

    address_request, options = transport.sent[-1]
    assert options["tag"] == ("config_write", "communication.address")
    assert address_request[0] == 1
    transport.complete_last(())
    assert transport.opened[-1] == ("COM8", 9600)
    assert transport.sent[-1][1]["tag"] == ("config_reconnect",)
    assert transport.sent[-1][0][0] == 7

    transport.complete_last(LEGACY_IDENTITY_REGISTERS)
    baud_request, options = transport.sent[-1]
    assert options["tag"] == ("config_write", "communication.baud_rate")
    assert baud_request[0] == 7
    transport.complete_last(())
    assert transport.opened[-1] == ("COM8", 19200)
    assert transport.sent[-1][1]["tag"] == ("config_reconnect",)
    transport.complete_last(identity_registers())
    assert transport.sent[-1][1]["tag"] == ("init", "x_config")
