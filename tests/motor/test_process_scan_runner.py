import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectrometer.motor.models import Axis, Direction
from spectrometer.motor.process_scan_runner import MotorScanRunner
from spectrometer.motor.scan import ScanMove
from spectrometer.qt import QtCore, Signal


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
    return _APPLICATION


class FakeMotor(QtCore.QObject):
    motion_finished = Signal(str, bool, str)

    def __init__(self):
        super().__init__()
        self.calls = []
        self.stop_count = 0

    def move_scan_segment(
        self,
        axis,
        pulses,
        direction,
        *,
        predictive,
        returning,
    ):
        operation_id = f"operation-{len(self.calls) + 1}"
        self.calls.append(
            (
                operation_id,
                Axis(axis),
                int(pulses),
                Direction(direction),
                bool(predictive),
                bool(returning),
            )
        )
        return operation_id

    def finish(self, index=-1, *, success=True, reason="completed"):
        self.motion_finished.emit(self.calls[index][0], success, reason)

    def stop(self):
        self.stop_count += 1


def move(axis, direction, *, pulses=320, dwell=0.0, logical_substeps=1):
    return ScanMove(
        Axis(axis),
        Direction(direction),
        int(pulses),
        float(dwell),
        True,
        logical_substeps=int(logical_substeps),
    )


def test_scan_path_chains_inside_runner_and_only_final_segment_is_strict():
    application()
    motor = FakeMotor()
    runner = MotorScanRunner(motor)
    started = []
    finished = []
    runner.segment_started.connect(lambda payload: started.append(dict(payload)))
    runner.path_finished.connect(
        lambda path_id, success, reason: finished.append(
            (path_id, success, reason)
        )
    )

    assert runner.start_path(
        "scan-1",
        (
            move(Axis.X, Direction.POSITIVE, logical_substeps=10),
            move(Axis.Y, Direction.POSITIVE),
            move(Axis.X, Direction.NEGATIVE),
        ),
        returning=False,
    )
    assert [call[4] for call in motor.calls] == [True]

    motor.finish()
    assert [call[4] for call in motor.calls] == [True, True]
    motor.finish()
    assert [call[4] for call in motor.calls] == [True, True, False]
    motor.finish()

    assert finished == [("scan-1", True, "completed")]
    assert runner.active is False
    assert [item["segment_number"] for item in started] == [1, 2, 3]
    assert started[0]["logical_substeps"] == 10
    assert all(item["scheduled_monotonic_ns"] > 0 for item in started)


def test_return_path_is_strict_for_every_segment():
    application()
    motor = FakeMotor()
    runner = MotorScanRunner(motor)

    assert runner.start_path(
        "return-1",
        (
            move(Axis.X, Direction.NEGATIVE),
            move(Axis.Y, Direction.NEGATIVE),
        ),
        returning=True,
    )
    motor.finish()
    motor.finish()

    assert [call[4] for call in motor.calls] == [False, False]
    assert [call[5] for call in motor.calls] == [True, True]


def test_positive_dwell_is_owned_by_runner_event_loop():
    application()
    motor = FakeMotor()
    runner = MotorScanRunner(motor)

    assert runner.start_path(
        "dwell-1",
        (
            move(Axis.X, Direction.POSITIVE, dwell=0.02),
            move(Axis.Y, Direction.POSITIVE),
        ),
        returning=False,
    )
    motor.finish()
    assert len(motor.calls) == 1

    deadline = QtCore.QDeadlineTimer(250)
    while len(motor.calls) == 1 and not deadline.hasExpired():
        application().processEvents()

    assert len(motor.calls) == 2


def test_motion_failure_aborts_remaining_path_once():
    application()
    motor = FakeMotor()
    runner = MotorScanRunner(motor)
    finished = []
    runner.path_finished.connect(
        lambda path_id, success, reason: finished.append(
            (path_id, success, reason)
        )
    )

    assert runner.start_path(
        "failed-1",
        (
            move(Axis.X, Direction.POSITIVE),
            move(Axis.Y, Direction.POSITIVE),
        ),
        returning=False,
    )
    motor.finish(success=False, reason="limit_fault")
    motor.finish(success=False, reason="duplicate")

    assert len(motor.calls) == 1
    assert finished == [("failed-1", False, "limit_fault")]


def test_stale_motion_signal_cannot_advance_new_path_and_stop_is_bounded():
    application()
    motor = FakeMotor()
    runner = MotorScanRunner(motor)
    finished = []
    runner.path_finished.connect(
        lambda path_id, success, reason: finished.append(
            (path_id, success, reason)
        )
    )

    assert runner.start_path(
        "stop-1",
        (
            move(Axis.X, Direction.POSITIVE),
            move(Axis.Y, Direction.POSITIVE),
        ),
        returning=False,
    )
    first_operation = motor.calls[0][0]
    assert runner.stop("user_stop")
    assert motor.stop_count == 1
    assert finished == [("stop-1", False, "user_stop")]

    assert runner.start_path(
        "scan-2",
        (move(Axis.Y, Direction.POSITIVE),),
        returning=False,
    )
    motor.motion_finished.emit(first_operation, True, "stale")
    assert len(motor.calls) == 2
    motor.finish()
    assert finished[-1] == ("scan-2", True, "completed")
