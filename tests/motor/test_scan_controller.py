import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectrometer.domain.enums import StorageFormat, SyncMode
from spectrometer.domain.models import AcquisitionRequest
from spectrometer.motor.models import (
    Axis,
    Direction,
    MotorAxisStatus,
    MotorStatus,
    Position,
)
from spectrometer.motor.scan import ScanParameters
from spectrometer.motor.scan_controller import ScanController, ScanState
from spectrometer.qt import QtCore, QtWidgets, Signal


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = (
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    )
    return _APPLICATION


def _axis_status(axis, position):
    return MotorAxisStatus(
        axis=axis,
        device_position_pulses=round(position * 320),
        software_position_mm=position,
        calibrated=True,
        moving=False,
        zero_limit_active=False,
        stop_reason="DONE",
        mechanical_position_mm=position,
    )


class FakeMotorController(QtCore.QObject):
    motion_finished = Signal(str, bool, str)
    connection_changed = Signal(bool, str)
    operation_failed = Signal(str)

    def __init__(self, start=Position(0, 0)):
        super().__init__()
        self.connected = True
        self.device_id = "MOTOR-TEST"
        self.motion_active = False
        self.status = MotorStatus(
            _axis_status(Axis.X, start.x_mm),
            _axis_status(Axis.Y, start.y_mm),
        )
        self.moves = []
        self.stop_count = 0
        self._active = None

    def move_relative(self, axis, distance_mm, direction):
        operation_id = f"motor-{len(self.moves) + 1}"
        self.moves.append((Axis(axis), float(distance_mm), Direction(direction)))
        self.motion_active = True
        self._active = (operation_id, Axis(axis), distance_mm, Direction(direction))
        return operation_id

    def finish_motion(self, success=True, reason="completed"):
        operation_id, axis, distance, direction = self._active
        if success:
            positions = {
                Axis.X: self.status.x.position_mm,
                Axis.Y: self.status.y.position_mm,
            }
            positions[axis] += distance * direction.sign
            self.status = MotorStatus(
                _axis_status(Axis.X, positions[Axis.X]),
                _axis_status(Axis.Y, positions[Axis.Y]),
            )
        self.motion_active = False
        self._active = None
        self.motion_finished.emit(operation_id, success, reason)

    def stop(self):
        self.stop_count += 1
        active = self._active
        self.motion_active = False
        self._active = None
        if active is not None:
            self.motion_finished.emit(active[0], False, "user_stop")

    def set_scan_active(self, active):
        self.scan_active = bool(active)


class FakeAcquisitionController(QtCore.QObject):
    task_started = Signal(str, object)
    task_finished = Signal(str, object, object)
    operation_rejected = Signal(str)

    def __init__(self):
        super().__init__()
        self.starts = []
        self.stop_count = 0
        self.global_task_id = None
        self._request = None

    def start_scan_global(self, device_ids, mode, sync_mode, **kwargs):
        self.global_task_id = f"spectrum-{len(self.starts) + 1}"
        self._request = AcquisitionRequest.create(
            "scan",
            device_ids,
            mode=mode,
            sync_mode=sync_mode,
            auto_store=kwargs["auto_store"],
            storage_format=kwargs["storage_format"],
            batch_size=kwargs["batch_size"],
        )
        self.starts.append((tuple(device_ids), mode, sync_mode, dict(kwargs)))
        return True

    def start_task(self):
        self.task_started.emit(self.global_task_id, self._request)

    def stop_global(self):
        self.stop_count += 1
        return self.global_task_id is not None

    def finish_task(self, files=(), failed=False):
        task_id = self.global_task_id
        self.global_task_id = None
        self.task_finished.emit(task_id, list(files), failed)


class SynchronousFakeAcquisitionController(FakeAcquisitionController):
    def start_scan_global(self, device_ids, mode, sync_mode, **kwargs):
        accepted = super().start_scan_global(
            device_ids, mode, sync_mode, **kwargs
        )
        self.start_task()
        return accepted


def _controller(tmp_path, parameters=None):
    motor = FakeMotorController()
    acquisition = FakeAcquisitionController()
    controller = ScanController(
        motor,
        acquisition,
        manifest_directory=tmp_path,
    )
    parameters = parameters or ScanParameters(1, 1, 1, 1)
    assert controller.start(
        parameters,
        device_ids=(0,),
        sync_mode=SyncMode.INDEPENDENT,
        storage_format=StorageFormat.CSV,
        batch_size=50,
    )
    return controller, motor, acquisition


def test_each_round_acquires_continuously_then_returns_without_acquisition(
    tmp_path,
):
    application()
    controller, motor, acquisition = _controller(tmp_path)

    assert controller.state is ScanState.STARTING_ACQUISITION
    assert acquisition.starts[0][3]["auto_store"] is True
    assert motor.moves == []

    acquisition.start_task()
    assert controller.state is ScanState.SCANNING
    for _ in range(3):
        motor.finish_motion()

    assert acquisition.stop_count == 1
    assert controller.state is ScanState.STOPPING_ACQUISITION
    assert len(motor.moves) == 3

    acquisition.finish_task(files=["round-1.csv"])
    assert controller.state is ScanState.RETURNING
    assert motor.moves[-1] == (Axis.Y, 1.0, Direction.NEGATIVE)
    motor.finish_motion()

    assert controller.state is ScanState.COMPLETED
    assert len(acquisition.starts) == 1
    assert len(motor.moves) == 4

    manifest = json.loads(controller.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert manifest["rounds"][0]["files"] == ["round-1.csv"]
    assert manifest["rounds"][0]["completed"] is True


def test_motor_only_scan_runs_and_returns_without_acquisition_or_manifest(
    tmp_path,
):
    application()
    motor = FakeMotorController()
    acquisition = FakeAcquisitionController()
    controller = ScanController(
        motor,
        acquisition,
        manifest_directory=tmp_path,
    )
    events = []
    controller.scan_event.connect(
        lambda event, payload: events.append((event, dict(payload)))
    )

    assert controller.start(
        ScanParameters(1, 1, 1, 1),
        device_ids=(),
        storage_format=StorageFormat.CSV,
        batch_size=50,
    )
    assert controller.state is ScanState.SCANNING
    assert controller.acquisition_enabled is False
    assert acquisition.starts == []
    assert len(motor.moves) == 1

    for _ in range(3):
        motor.finish_motion()
    assert controller.state is ScanState.RETURNING
    assert acquisition.stop_count == 0
    assert motor.moves[-1] == (Axis.Y, 1.0, Direction.NEGATIVE)

    motor.finish_motion()
    assert controller.state is ScanState.COMPLETED
    assert acquisition.starts == []
    assert acquisition.stop_count == 0
    assert controller.manifest_path is None
    assert list(tmp_path.glob("scan-*.json")) == []
    assert [event for event, _payload in events] == [
        "motor_scan_started",
        "motor_scan_round_started",
        "motor_scan_round_completed",
        "motor_scan_finished",
    ]
    assert events[0][1]["mode"] == "motor_only"
    assert events[-1][1]["status"] == "completed"


def test_motor_only_scan_starts_next_round_after_return(tmp_path):
    application()
    motor = FakeMotorController()
    acquisition = FakeAcquisitionController()
    controller = ScanController(
        motor,
        acquisition,
        manifest_directory=tmp_path,
    )
    assert controller.start(
        ScanParameters(1, 1, 1, 2),
        device_ids=(),
    )

    for _ in range(4):
        motor.finish_motion()

    assert controller.state is ScanState.SCANNING
    assert len(motor.moves) == 5
    assert acquisition.starts == []


def test_motor_only_stop_and_fault_do_not_wait_for_acquisition(tmp_path):
    application()
    motor = FakeMotorController()
    acquisition = FakeAcquisitionController()
    controller = ScanController(
        motor,
        acquisition,
        manifest_directory=tmp_path,
    )
    finished = []
    controller.scan_finished.connect(
        lambda success, reason, manifest: finished.append(
            (success, reason, manifest)
        )
    )
    assert controller.start(ScanParameters(1, 1, 1, 1), device_ids=())
    assert controller.stop()
    assert controller.state is ScanState.IDLE
    assert finished[-1] == (False, "user_stop", None)
    assert acquisition.stop_count == 0

    assert controller.start(ScanParameters(1, 1, 1, 1), device_ids=())
    motor.finish_motion(success=False, reason="limit_fault")
    assert controller.state is ScanState.FAULTED
    assert finished[-1] == (False, "limit_fault", None)
    assert acquisition.stop_count == 0


def test_next_round_starts_only_after_save_and_return_complete(tmp_path):
    application()
    parameters = ScanParameters(1, 1, 1, 2)
    controller, motor, acquisition = _controller(tmp_path, parameters)

    acquisition.start_task()
    for _ in range(3):
        motor.finish_motion()
    acquisition.finish_task(files=["round-1.csv"])
    assert len(acquisition.starts) == 1

    motor.finish_motion()
    assert len(acquisition.starts) == 2
    assert controller.state is ScanState.STARTING_ACQUISITION


def test_motor_failure_stops_acquisition_and_does_not_auto_return(tmp_path):
    application()
    controller, motor, acquisition = _controller(tmp_path)
    acquisition.start_task()
    motor.finish_motion(success=False, reason="limit_fault")

    assert acquisition.stop_count == 1
    acquisition.finish_task(files=["partial.zgs"], failed=True)

    assert controller.state is ScanState.FAULTED
    assert len(motor.moves) == 1
    manifest = json.loads(controller.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "faulted"
    assert manifest["rounds"][0]["completed"] is False
    assert manifest["rounds"][0]["files"] == ["partial.zgs"]


def test_user_stop_does_not_return_or_start_another_round(tmp_path):
    application()
    controller, motor, acquisition = _controller(
        tmp_path, ScanParameters(1, 1, 1, 2)
    )
    acquisition.start_task()

    controller.stop()
    acquisition.finish_task(files=["partial.zgs"], failed=True)

    assert controller.state is ScanState.IDLE
    assert len(acquisition.starts) == 1
    assert len(motor.moves) == 1
    assert motor.stop_count == 1


def test_synchronous_task_started_signal_is_not_lost(tmp_path):
    application()
    motor = FakeMotorController()
    acquisition = SynchronousFakeAcquisitionController()
    controller = ScanController(
        motor,
        acquisition,
        manifest_directory=tmp_path,
    )

    assert controller.start(
        ScanParameters(1, 1, 1, 1),
        device_ids=(0,),
        storage_format=StorageFormat.CSV,
        batch_size=50,
    )
    assert controller.state is ScanState.SCANNING
    assert len(motor.moves) == 1
