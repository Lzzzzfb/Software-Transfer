"""Qt state machine coordinating raster motion and spectrum acquisition."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ..domain.enums import (
    AcquisitionMode,
    AcquisitionOwner,
    StorageFormat,
    SyncMode,
)
from ..qt import QtCore, Signal, Slot
from .manifest import ScanManifestWriter
from .models import Position
from .scan import ScanMove, ScanParameters, ScanPlan, build_scan_plan


class ScanState(str, Enum):
    IDLE = "idle"
    PRECHECK = "precheck"
    STARTING_ACQUISITION = "starting_acquisition"
    SCANNING = "scanning"
    DWELLING = "dwelling"
    STOPPING_ACQUISITION = "stopping_acquisition"
    RETURNING = "returning"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAULTED = "faulted"


@dataclass(frozen=True)
class ScanAcquisitionConfig:
    device_ids: tuple[int, ...]
    sync_mode: SyncMode
    master_device_id: int | None
    storage_format: StorageFormat
    batch_size: int


class ScanController(QtCore.QObject):
    state_changed = Signal(object)
    progress_changed = Signal(int, int, int, int)
    operation_failed = Signal(str)
    scan_finished = Signal(bool, str, object)

    def __init__(
        self,
        motor_controller,
        acquisition_controller,
        *,
        manifest_directory,
        parent=None,
    ):
        super().__init__(parent)
        self.motor = motor_controller
        self.acquisition = acquisition_controller
        self._manifest = ScanManifestWriter(manifest_directory)
        self._state = ScanState.IDLE
        self._plan: ScanPlan | None = None
        self._config: ScanAcquisitionConfig | None = None
        self._round_index = 0
        self._move_index = 0
        self._current_move: ScanMove | None = None
        self._current_operation_id = ""
        self._current_task_id = ""
        self._round_files: list[str] = []
        self._failure_reason = ""
        self._user_stopping = False
        self._finished_emitted = False

        self._dwell_timer = QtCore.QTimer(self)
        self._dwell_timer.setSingleShot(True)
        self._dwell_timer.timeout.connect(self._after_dwell)

        self.motor.motion_finished.connect(self._on_motion_finished)
        if hasattr(self.motor, "connection_changed"):
            self.motor.connection_changed.connect(
                self._on_motor_connection_changed
            )
        self.acquisition.task_started.connect(self._on_task_started)
        self.acquisition.task_finished.connect(self._on_task_finished)
        self.acquisition.operation_rejected.connect(
            self._on_acquisition_rejected
        )

    @property
    def state(self) -> ScanState:
        return self._state

    @property
    def active(self) -> bool:
        return self._state not in (
            ScanState.IDLE,
            ScanState.COMPLETED,
            ScanState.FAULTED,
        )

    @property
    def plan(self) -> ScanPlan | None:
        return self._plan

    @property
    def manifest_path(self):
        return self._manifest.path

    def set_manifest_directory(self, directory) -> bool:
        if self.active:
            return False
        self._manifest.directory = Path(directory)
        return True

    def _set_state(self, state: ScanState):
        state = ScanState(state)
        if self._state is state:
            return
        self._state = state
        self.state_changed.emit(state)

    def start(
        self,
        parameters: ScanParameters,
        *,
        device_ids,
        sync_mode=SyncMode.INDEPENDENT,
        master_device_id=None,
        storage_format=StorageFormat.CSV_EXCEL,
        batch_size: int = 500,
        allow_uncalibrated: bool = False,
    ) -> bool:
        if self.active:
            self.operation_failed.emit("扫描任务正在运行")
            return False
        self._set_state(ScanState.PRECHECK)
        try:
            if not self.motor.connected:
                raise ValueError("电机控制器未连接")
            if self.motor.motion_active:
                raise ValueError("电机正在执行其他运动")
            if not getattr(self.motor, "mechanics_valid", True):
                raise ValueError(
                    "驱动板机械参数与 15 mm / 4800 pulse 换算不一致"
                )
            status = self.motor.status
            if status.fault_latched:
                raise ValueError("电机存在未清除故障")
            calibrated = status.x.calibrated and status.y.calibrated
            if not calibrated and not allow_uncalibrated:
                raise ValueError(
                    "X/Y 坐标未校准，需确认风险后才能扫描"
                )
            ids = tuple(int(device_id) for device_id in device_ids)
            if not ids:
                raise ValueError("没有参与扫描采集的光谱仪")
            start_position = (
                Position(
                    status.x.mechanical_position_mm,
                    status.y.mechanical_position_mm,
                )
                if calibrated else None
            )
            plan = build_scan_plan(parameters, start=start_position)
            config = ScanAcquisitionConfig(
                device_ids=ids,
                sync_mode=SyncMode(sync_mode),
                master_device_id=master_device_id,
                storage_format=StorageFormat(storage_format),
                batch_size=int(batch_size),
            )
            if not 1 <= config.batch_size <= 1000:
                raise ValueError("batch_size 必须在 1–1000 之间")
        except (TypeError, ValueError) as exc:
            self._set_state(ScanState.IDLE)
            self.operation_failed.emit(str(exc))
            return False

        self._plan = plan
        self._config = config
        self._round_index = 0
        self._move_index = 0
        self._current_move = None
        self._current_operation_id = ""
        self._current_task_id = ""
        self._round_files = []
        self._failure_reason = ""
        self._user_stopping = False
        self._finished_emitted = False
        if hasattr(self.motor, "set_scan_active"):
            self.motor.set_scan_active(True)
        self._manifest.start(
            plan,
            motor_device_id=self.motor.device_id,
            spectrometer_device_ids=config.device_ids,
        )
        self._start_round()
        return True

    def _current_round(self):
        return self._plan.rounds[self._round_index]

    def _start_round(self):
        self._move_index = 0
        self._current_move = None
        self._current_operation_id = ""
        self._current_task_id = ""
        self._round_files = []
        self._set_state(ScanState.STARTING_ACQUISITION)
        accepted = self.acquisition.start_scan_global(
            self._config.device_ids,
            AcquisitionMode.CONTINUOUS,
            self._config.sync_mode,
            master_device_id=self._config.master_device_id,
            auto_store=True,
            storage_format=self._config.storage_format,
            batch_size=self._config.batch_size,
        )
        if not accepted:
            self._begin_fault("光谱仪扫描采集启动被拒绝")
            return
        self._current_task_id = (
            getattr(self.acquisition, "global_task_id", "") or ""
        )

    @Slot(str, object)
    def _on_task_started(self, task_id: str, request):
        if request.owner is not AcquisitionOwner.SCAN:
            return
        task_id = str(task_id)
        if (
            not self._current_task_id
            and self._state is ScanState.STARTING_ACQUISITION
        ):
            # AcquisitionController may emit task_started synchronously before
            # start_scan_global() returns and exposes global_task_id.
            self._current_task_id = task_id
        if task_id != self._current_task_id:
            return
        self._manifest.acquisition_started(
            self._current_round().index, task_id
        )
        if self._user_stopping or self._failure_reason:
            self.acquisition.stop_global()
            return
        self._move_index = 0
        self._set_state(ScanState.SCANNING)
        self._advance_scan()

    def _begin_move(self, move: ScanMove, *, returning: bool):
        self._current_move = move
        self._set_state(
            ScanState.RETURNING if returning else ScanState.SCANNING
        )
        operation_id = self.motor.move_relative(
            move.axis,
            move.distance_mm,
            move.direction,
        )
        if not operation_id:
            self._begin_fault(
                f"{move.axis.value} 轴运动命令未被接受"
            )
            return
        self._current_operation_id = operation_id
        round_number = self._round_index + 1
        self.progress_changed.emit(
            round_number,
            len(self._plan.rounds),
            self._move_index + 1,
            (
                len(self._current_round().return_moves)
                if returning
                else len(self._current_round().scan_moves)
            ),
        )

    def _advance_scan(self):
        if self._user_stopping or self._failure_reason:
            return
        moves = self._current_round().scan_moves
        if self._move_index >= len(moves):
            self._stop_round_acquisition()
            return
        self._begin_move(moves[self._move_index], returning=False)

    def _advance_return(self):
        if self._user_stopping or self._failure_reason:
            return
        moves = self._current_round().return_moves
        if self._move_index >= len(moves):
            self._manifest.return_finished(
                self._current_round().index,
                completed=True,
            )
            self._round_index += 1
            if self._round_index >= len(self._plan.rounds):
                self._complete()
            else:
                self._start_round()
            return
        self._begin_move(moves[self._move_index], returning=True)

    @Slot(str, bool, str)
    def _on_motion_finished(
        self, operation_id: str, success: bool, reason: str
    ):
        if operation_id != self._current_operation_id:
            return
        move = self._current_move
        returning = self._state is ScanState.RETURNING
        self._current_operation_id = ""
        self._current_move = None
        if self._user_stopping:
            return
        if not success:
            self._begin_fault(str(reason) or "电机运动失败")
            return
        self._move_index += 1
        if returning:
            self._advance_return()
            return
        if move is not None and move.dwell_after_seconds > 0.0:
            self._set_state(ScanState.DWELLING)
            self._dwell_timer.start(
                max(1, round(move.dwell_after_seconds * 1000))
            )
        else:
            self._advance_scan()

    @Slot()
    def _after_dwell(self):
        if not self._user_stopping and not self._failure_reason:
            self._advance_scan()

    def _stop_round_acquisition(self):
        self._set_state(ScanState.STOPPING_ACQUISITION)
        if not self.acquisition.stop_global():
            self._begin_fault("无法停止光谱仪扫描采集")

    @Slot(str, object, object)
    def _on_task_finished(self, task_id: str, files, failed):
        if str(task_id) != self._current_task_id:
            return
        self._round_files = [str(path) for path in files]
        reason = self._failure_reason or (
            "光谱仪采集或保存失败" if bool(failed) else ""
        )
        acquisition_failed = bool(failed) or bool(self._failure_reason)
        self._manifest.acquisition_finished(
            self._current_round().index,
            self._round_files,
            failed=acquisition_failed,
            reason=reason,
        )
        self._current_task_id = ""

        if self._user_stopping:
            self._finish_stopped("user_stop")
            return
        if acquisition_failed:
            if not self._failure_reason:
                self._failure_reason = reason
            self._finish_fault()
            return
        if self._state is not ScanState.STOPPING_ACQUISITION:
            self._begin_fault("光谱仪任务提前结束")
            return
        self._move_index = 0
        self._set_state(ScanState.RETURNING)
        self._advance_return()

    @Slot(str)
    def _on_acquisition_rejected(self, message: str):
        if self.active and not self._failure_reason:
            self._begin_fault(str(message))

    @Slot(bool, str)
    def _on_motor_connection_changed(self, connected: bool, detail: str):
        if self.active and not connected:
            self._begin_fault(
                str(detail) or "扫描过程中电机连接断开"
            )

    def stop(self):
        if not self.active:
            return False
        self._user_stopping = True
        self._dwell_timer.stop()
        self._set_state(ScanState.STOPPING)
        self._current_operation_id = ""
        self._current_move = None
        self.motor.stop()
        if self._current_task_id:
            if not self.acquisition.stop_global():
                self._finish_stopped("user_stop")
        else:
            self._finish_stopped("user_stop")
        return True

    def _begin_fault(self, reason: str):
        if self._finished_emitted:
            return
        self._failure_reason = str(reason) or "scan_fault"
        self._dwell_timer.stop()
        self._set_state(ScanState.STOPPING)
        self._current_operation_id = ""
        self._current_move = None
        if getattr(self.motor, "motion_active", False):
            self.motor.stop()
        if self._current_task_id:
            if self.acquisition.stop_global():
                return
        self._finish_fault()

    def _finish_fault(self):
        if self._plan is not None and self._round_index < len(self._plan.rounds):
            self._manifest.return_finished(
                self._current_round().index,
                completed=False,
                reason=self._failure_reason,
            )
        self._manifest.finish("faulted", self._failure_reason)
        self._set_state(ScanState.FAULTED)
        self.operation_failed.emit(self._failure_reason)
        self._emit_finished(False, self._failure_reason)

    def _finish_stopped(self, reason: str):
        if self._plan is not None and self._round_index < len(self._plan.rounds):
            self._manifest.return_finished(
                self._current_round().index,
                completed=False,
                reason=reason,
            )
        self._manifest.finish("stopped", reason)
        self._set_state(ScanState.IDLE)
        self._emit_finished(False, reason)

    def _complete(self):
        self._manifest.finish("completed")
        self._set_state(ScanState.COMPLETED)
        self._emit_finished(True, "completed")

    def _emit_finished(self, success: bool, reason: str):
        if self._finished_emitted:
            return
        if hasattr(self.motor, "set_scan_active"):
            self.motor.set_scan_active(False)
        self._finished_emitted = True
        self.scan_finished.emit(
            bool(success),
            str(reason),
            self.manifest_path,
        )
