import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectrometer.motor.models import Axis, Direction
from spectrometer.motor.process_worker import MotorProcessRuntime
from spectrometer.motor.scan import ScanMove
from spectrometer.qt import QtCore, Signal


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
    return _APPLICATION


class EventQueue:
    def __init__(self):
        self.items = []

    def put_nowait(self, item):
        self.items.append(item)


class FakeController(QtCore.QObject):
    candidates_changed = Signal(object)
    connection_changed = Signal(bool, str)
    status_changed = Signal(object)
    configuration_changed = Signal(object)
    operation_failed = Signal(str)
    diagnostic_event = Signal(str)
    motion_started = Signal(str)
    motion_finished = Signal(str, bool, str)
    configuration_apply_finished = Signal(bool, str)
    speed_applied = Signal(object, int, int, str)
    scan_round_prepared = Signal(bool, str)
    scan_round_verified = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self.connected = True
        self.device_id = "MOTOR-FAKE"
        self.status = "status"
        self.configuration = "configuration"
        self.host_settings = "host"
        self.motion_active = False
        self.scan_active = False
        self.safety_locked = False
        self.mechanics_valid = True
        self.calls = []
        self.stop_count = 0

    def set_scan_active(self, active):
        self.scan_active = bool(active)
        self.calls.append(("set_scan_active", bool(active)))
        return True

    def prepare_scan_round(self):
        self.calls.append(("prepare_scan_round",))
        return False

    def move_scan_segment(
        self, axis, pulses, direction, *, predictive, returning
    ):
        operation_id = f"move-{len(self.calls)}"
        self.calls.append(
            (
                "move",
                Axis(axis),
                int(pulses),
                Direction(direction),
                bool(predictive),
                bool(returning),
            )
        )
        return operation_id

    def stop(self):
        self.stop_count += 1
        return True

    def shutdown(self):
        self.calls.append(("shutdown",))


def test_runtime_dispatches_allowlisted_commands_and_emits_snapshot():
    application()
    events = EventQueue()
    controller = FakeController()
    runtime = MotorProcessRuntime(
        None,
        events,
        controller=controller,
        application=application(),
    )

    runtime.handle_message(("command", 7, "set_scan_active", (True,), {}))

    assert controller.scan_active is True
    assert ("command_result", 7, "set_scan_active", True) in events.items
    snapshots = [item[1] for item in events.items if item[0] == "snapshot"]
    assert snapshots[-1]["device_id"] == "MOTOR-FAKE"
    assert snapshots[-1]["scan_active"] is True


def test_runtime_rejects_unknown_command_without_calling_controller():
    application()
    events = EventQueue()
    controller = FakeController()
    runtime = MotorProcessRuntime(
        None,
        events,
        controller=controller,
        application=application(),
    )

    runtime.handle_message(("command", 8, "not_allowed", (), {}))

    assert (
        "command_result",
        8,
        "not_allowed",
        False,
        "unsupported_motor_command",
    ) in events.items
    assert controller.calls == []


def test_runtime_executes_whole_scan_path_and_reports_segments():
    application()
    events = EventQueue()
    controller = FakeController()
    runtime = MotorProcessRuntime(
        None,
        events,
        controller=controller,
        application=application(),
    )
    moves = (
        ScanMove(Axis.X, Direction.POSITIVE, 320, 0.0, True),
        ScanMove(Axis.Y, Direction.POSITIVE, 320, 0.0, True),
    )

    runtime.handle_message(
        ("command", 9, "start_scan_path", ("path-1", moves), {"returning": False})
    )
    assert controller.calls[-1][-2:] == (True, False)
    first_operation = runtime.runner._operation_id
    controller.motion_finished.emit(first_operation, True, "completed")
    assert controller.calls[-1][-2:] == (False, False)
    controller.motion_finished.emit(
        runtime.runner._operation_id, True, "completed"
    )

    assert any(item[:2] == ("path_finished", "path-1") for item in events.items)
    assert len([item for item in events.items if item[0] == "segment_started"]) == 2


def test_failed_round_preparation_gets_explicit_result_signal():
    application()
    events = EventQueue()
    runtime = MotorProcessRuntime(
        None,
        events,
        controller=FakeController(),
        application=application(),
    )

    runtime.handle_message(("command", 10, "prepare_scan_round", (), {}))

    assert (
        "scan_round_prepared",
        False,
        "scan_round_prepare_not_accepted",
    ) in events.items

