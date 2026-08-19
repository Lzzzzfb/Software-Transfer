"""Qt state machine coordinating raster motion and spectrum acquisition."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
import uuid

from ..domain.enums import (
    AcquisitionMode,
    AcquisitionOwner,
    StorageFormat,
    SyncMode,
)
from ..qt import QtCore, Signal, Slot
from ..square_wave.models import (
    OutputOwner,
    OutputState,
    SquareWaveParameters,
)
from .manifest import ScanManifestWriter
from .models import Position
from .scan import ScanMove, ScanParameters, ScanPlan, build_scan_plan


class ScanState(str, Enum):
    IDLE = "idle"
    PRECHECK = "precheck"
    STARTING_SIGNAL = "starting_signal"
    STARTING_ACQUISITION = "starting_acquisition"
    SCANNING = "scanning"
    DWELLING = "dwelling"
    STOPPING_ACQUISITION = "stopping_acquisition"
    STOPPING_SIGNAL = "stopping_signal"
    RETURNING = "returning"
    EXPORTING = "exporting"
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

    @property
    def enabled(self) -> bool:
        return bool(self.device_ids)


class ScanController(QtCore.QObject):
    state_changed = Signal(object)
    progress_changed = Signal(int, int, int, int)
    operation_failed = Signal(str)
    scan_finished = Signal(bool, str, object)
    scan_event = Signal(str, object)

    def __init__(
        self,
        motor_controller,
        acquisition_controller,
        *,
        square_wave_controller=None,
        manifest_directory,
        parent=None,
    ):
        super().__init__(parent)
        self.motor = motor_controller
        self.acquisition = acquisition_controller
        self.square_wave = square_wave_controller
        self._manifest = ScanManifestWriter(manifest_directory)
        self._manifest_active = False
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
        self._scan_id = ""
        self._waiting_round_verification = False
        self._active_path_id = ""
        self._path_returning = False
        self._deferred_export_task_ids: list[str] = []
        self._deferred_task_rounds: dict[str, int] = {}
        self._pending_export_task_ids: set[str] = set()
        self._finish_after_exports = ""
        self._export_failure_reason = ""
        self._square_wave_enabled = False
        self._square_wave_parameters: SquareWaveParameters | None = None
        self._square_wave_active = False
        self._after_square_wave_stop = ""

        self._dwell_timer = QtCore.QTimer(self)
        self._dwell_timer.setSingleShot(True)
        self._dwell_timer.timeout.connect(self._after_dwell)

        self.motor.motion_finished.connect(self._on_motion_finished)
        if hasattr(self.motor, "scan_round_prepared"):
            self.motor.scan_round_prepared.connect(
                self._on_scan_round_prepared
            )
        if hasattr(self.motor, "scan_round_verified"):
            self.motor.scan_round_verified.connect(
                self._on_scan_round_verified
            )
        if hasattr(self.motor, "connection_changed"):
            self.motor.connection_changed.connect(
                self._on_motor_connection_changed
            )
        if hasattr(self.motor, "path_segment_started"):
            self.motor.path_segment_started.connect(
                self._on_path_segment_started
            )
        if hasattr(self.motor, "path_segment_finished"):
            self.motor.path_segment_finished.connect(
                self._on_path_segment_finished
            )
        if hasattr(self.motor, "path_finished"):
            self.motor.path_finished.connect(self._on_path_finished)
        self.acquisition.task_started.connect(self._on_task_started)
        self.acquisition.task_finished.connect(self._on_task_finished)
        if hasattr(self.acquisition, "scan_capture_sealed"):
            self.acquisition.scan_capture_sealed.connect(
                self._on_scan_capture_sealed
            )
        self.acquisition.operation_rejected.connect(
            self._on_acquisition_rejected
        )
        if self.square_wave is not None:
            self.square_wave.scan_round_prepared.connect(
                self._on_square_wave_prepared
            )
            self.square_wave.scan_round_finished.connect(
                self._on_square_wave_finished
            )
            if hasattr(self.square_wave, "connection_changed"):
                self.square_wave.connection_changed.connect(
                    self._on_square_wave_connection_changed
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
        return self._manifest.path if self._manifest_active else None

    @property
    def acquisition_enabled(self) -> bool:
        return bool(self._config and self._config.enabled)

    @property
    def scan_id(self) -> str:
        return self._scan_id

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

    def _emit_scan_event(self, event: str, **payload):
        self.scan_event.emit(
            str(event),
            {
                "scan_id": self._scan_id,
                "mode": (
                    "acquisition"
                    if self.acquisition_enabled
                    else "motor_only"
                ),
                **payload,
            },
        )

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
        square_wave_enabled: bool = False,
        square_wave_parameters: SquareWaveParameters | None = None,
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
            square_wave_enabled = bool(square_wave_enabled)
            if square_wave_enabled:
                if self.square_wave is None:
                    raise ValueError("未配置方波控制器")
                if not isinstance(
                    square_wave_parameters, SquareWaveParameters
                ):
                    raise ValueError("方波扫描参数无效")
                if not self.square_wave.connected:
                    raise ValueError("方波发生器未连接")
                if self.square_wave.owner is OutputOwner.MANUAL:
                    raise ValueError("方波正由手动操作占用")
                if self.square_wave.owner is not OutputOwner.NONE:
                    raise ValueError("方波已被其他扫描任务占用")
                if self.square_wave.output_state is OutputState.UNKNOWN:
                    raise ValueError(
                        "方波输出状态未知，请重新连接并完成停止确认"
                    )
                if self.square_wave.output_state is not OutputState.STOPPED:
                    raise ValueError("方波输出未确认关闭")
                if getattr(self.square_wave, "busy", False):
                    raise ValueError("方波控制器正在执行其他操作")
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
        self._scan_id = uuid.uuid4().hex
        self._waiting_round_verification = False
        self._active_path_id = ""
        self._path_returning = False
        self._deferred_export_task_ids = []
        self._deferred_task_rounds = {}
        self._pending_export_task_ids = set()
        self._finish_after_exports = ""
        self._export_failure_reason = ""
        self._square_wave_enabled = square_wave_enabled
        self._square_wave_parameters = (
            square_wave_parameters if square_wave_enabled else None
        )
        self._square_wave_active = False
        self._after_square_wave_stop = ""
        self._manifest_active = config.enabled
        if hasattr(self.motor, "set_scan_active"):
            if self.motor.set_scan_active(True) is False:
                self._set_state(ScanState.IDLE)
                self.operation_failed.emit("电机扫描任务锁定失败")
                return False
        if self._manifest_active:
            square_wave_manifest = {"enabled": False}
            if self._square_wave_enabled:
                identity = getattr(self.square_wave, "identity", None)
                square_wave_manifest = {
                    "enabled": True,
                    "port": str(getattr(self.square_wave, "port_name", "")),
                    "serial_number": str(
                        getattr(self.square_wave, "serial_number", "")
                    ),
                    "identity": (
                        asdict(identity) if identity is not None else None
                    ),
                    "parameters": asdict(self._square_wave_parameters),
                }
            self._manifest.start(
                plan,
                motor_device_id=self.motor.device_id,
                spectrometer_device_ids=config.device_ids,
                square_wave=square_wave_manifest,
            )
        self._emit_scan_event(
            "motor_scan_started",
            parameters=asdict(plan.parameters),
            start_position=asdict(plan.start),
            calibrated_start=bool(plan.calibrated_start),
            round_total=len(plan.rounds),
            spectrometer_device_ids=list(config.device_ids),
            square_wave_enabled=bool(self._square_wave_enabled),
            square_wave_parameters=(
                asdict(self._square_wave_parameters)
                if self._square_wave_parameters is not None
                else None
            ),
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
        self._waiting_round_verification = False
        self._active_path_id = ""
        self._path_returning = False
        self._emit_scan_event(
            "motor_scan_round_started",
            round_number=self._round_index + 1,
            round_total=len(self._plan.rounds),
            scan_move_total=len(self._current_round().scan_moves),
            return_move_total=len(self._current_round().return_moves),
        )
        self._set_state(ScanState.PRECHECK)
        if hasattr(self.motor, "prepare_scan_round"):
            if not self.motor.prepare_scan_round():
                self._begin_fault("无法读取并锁定本轮电机原始起点")
            return
        self._begin_round_after_motor_prepared()

    @Slot(bool, str)
    def _on_scan_round_prepared(self, success: bool, reason: str):
        if not self.active or self._state is not ScanState.PRECHECK:
            return
        if not success:
            self._begin_fault(str(reason) or "电机扫描轮次准备失败")
            return
        self._emit_scan_event(
            "motor_scan_round_prepared",
            round_number=self._round_index + 1,
            reason=str(reason),
        )
        self._begin_round_after_motor_prepared()

    def _begin_round_after_motor_prepared(self):
        if self._square_wave_enabled:
            self._set_state(ScanState.STARTING_SIGNAL)
            if not self.square_wave.prepare_scan_round(
                self._square_wave_parameters
            ):
                self._begin_fault("方波扫描轮次启动请求被拒绝")
            return
        self._begin_round_after_square_wave_started()

    @Slot(bool, str)
    def _on_square_wave_prepared(self, success: bool, reason: str):
        if (
            not self.active
            or not self._square_wave_enabled
            or self._state is not ScanState.STARTING_SIGNAL
        ):
            return
        if not success:
            if self._user_stopping:
                self._finish_stopped("user_stop")
            else:
                self._set_state(ScanState.STOPPING)
                self._begin_fault(str(reason) or "方波输出启动失败")
            return
        self._square_wave_active = True
        self._emit_scan_event(
            "motor_scan_square_wave_started",
            round_number=self._round_index + 1,
            parameters=asdict(self._square_wave_parameters),
            reason=str(reason),
        )
        if self._manifest_active:
            self._manifest.square_wave_started(
                self._current_round().index,
                parameters=asdict(self._square_wave_parameters),
                reason=str(reason),
            )
        if self._user_stopping:
            self._request_square_wave_stop("stopped")
            return
        if self._failure_reason:
            self._request_square_wave_stop("faulted")
            return
        self._begin_round_after_square_wave_started()

    def _begin_round_after_square_wave_started(self):
        if not self.acquisition_enabled:
            self._set_state(ScanState.SCANNING)
            self._start_scan_motion()
            return
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
        self._emit_scan_event(
            "motor_scan_acquisition_link",
            round_number=self._current_round().index,
            acquisition_id=task_id,
            task_id=task_id,
        )
        self._manifest.acquisition_started(
            self._current_round().index, task_id
        )
        if self._user_stopping or self._failure_reason:
            self.acquisition.stop_global()
            return
        self._move_index = 0
        self._set_state(ScanState.SCANNING)
        self._start_scan_motion()

    @property
    def _uses_process_path(self) -> bool:
        return all(
            hasattr(self.motor, name)
            for name in (
                "start_scan_path",
                "path_segment_started",
                "path_segment_finished",
                "path_finished",
            )
        )

    def _start_scan_motion(self):
        if not self._uses_process_path:
            self._advance_scan()
            return
        self._move_index = 0
        path_id = (
            f"{self._scan_id}:round:{self._round_index + 1}:scan"
        )
        self._active_path_id = path_id
        self._path_returning = False
        if not self.motor.start_scan_path(
            path_id,
            self._current_round().scan_moves,
            returning=False,
        ):
            self._active_path_id = ""
            self._begin_fault("电机扫描路径未被接受")

    def _begin_move(self, move: ScanMove, *, returning: bool):
        self._current_move = move
        self._set_state(
            ScanState.RETURNING if returning else ScanState.SCANNING
        )
        if hasattr(self.motor, "move_scan_segment"):
            final_scan_segment = (
                not returning
                and self._move_index
                == len(self._current_round().scan_moves) - 1
            )
            operation_id = self.motor.move_scan_segment(
                move.axis,
                move.pulses,
                move.direction,
                predictive=not final_scan_segment and not returning,
                returning=returning,
            )
        else:
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
        self._emit_scan_event(
            "motor_scan_segment_started",
            round_number=self._round_index + 1,
            segment_number=self._move_index + 1,
            segment_total=(
                len(self._current_round().return_moves)
                if returning
                else len(self._current_round().scan_moves)
            ),
            operation_id=str(operation_id),
            axis=move.axis.value,
            direction=move.direction.value,
            pulses=int(move.pulses),
            logical_substeps=int(move.logical_substeps),
            coalesced=bool(move.logical_substeps > 1),
            predictive=bool(not returning and not final_scan_segment)
            if hasattr(self.motor, "move_scan_segment")
            else False,
            returning=bool(returning),
        )
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
            self._verify_scan_round()
            return
        self._begin_move(moves[self._move_index], returning=False)

    def _advance_return(self):
        if self._user_stopping or self._failure_reason:
            return
        moves = self._current_round().return_moves
        if self._move_index >= len(moves):
            self._finish_return_path()
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
            self._emit_scan_event(
                "motor_scan_segment_finished",
                round_number=self._round_index + 1,
                segment_number=self._move_index + 1,
                operation_id=str(operation_id),
                logical_substeps=int(move.logical_substeps)
                if move is not None
                else 1,
                success=False,
                reason=str(reason),
                returning=bool(returning),
            )
            self._begin_fault(str(reason) or "电机运动失败")
            return
        self._emit_scan_event(
            "motor_scan_segment_finished",
            round_number=self._round_index + 1,
            segment_number=self._move_index + 1,
            operation_id=str(operation_id),
            logical_substeps=int(move.logical_substeps)
            if move is not None
            else 1,
            success=True,
            reason=str(reason),
            returning=bool(returning),
        )
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

    def _verify_scan_round(self):
        self._waiting_round_verification = True
        self._emit_scan_event(
            "motor_scan_round_verifying",
            round_number=self._round_index + 1,
        )
        if hasattr(self.motor, "verify_scan_round"):
            if not self.motor.verify_scan_round():
                self._waiting_round_verification = False
                self._begin_fault("无法启动扫描终点严格校验")
            return
        self._on_scan_round_verified(True, "legacy_verified")

    @Slot(bool, str)
    def _on_scan_round_verified(self, success: bool, reason: str):
        if not self.active or not self._waiting_round_verification:
            return
        self._waiting_round_verification = False
        if not success:
            self._begin_fault(str(reason) or "扫描终点严格校验失败")
            return
        self._emit_scan_event(
            "motor_scan_round_verified",
            round_number=self._round_index + 1,
            reason=str(reason),
        )
        if self.acquisition_enabled:
            self._stop_round_acquisition()
        else:
            self._request_square_wave_stop("return")

    @staticmethod
    def _stop_action_priority(action: str) -> int:
        return {"": 0, "return": 1, "stopped": 2, "faulted": 3}.get(
            str(action),
            3,
        )

    def _request_square_wave_stop(self, next_action: str):
        next_action = str(next_action)
        if not self._square_wave_enabled or not self._square_wave_active:
            self._continue_after_square_wave_stop(next_action)
            return
        if self._state is ScanState.STOPPING_SIGNAL:
            if self._stop_action_priority(next_action) > self._stop_action_priority(
                self._after_square_wave_stop
            ):
                self._after_square_wave_stop = next_action
            return
        self._after_square_wave_stop = next_action
        self._set_state(ScanState.STOPPING_SIGNAL)
        accepted = self.square_wave.finish_scan_round()
        if not accepted and self._state is ScanState.STOPPING_SIGNAL:
            self._square_wave_active = False
            self._failure_reason = (
                self._failure_reason
                or "方波停止请求被拒绝，输出状态未知"
            )
            self._emit_scan_event(
                "motor_scan_square_wave_stopped",
                round_number=self._round_index + 1,
                success=False,
                reason=self._failure_reason,
            )
            if self._manifest_active:
                self._manifest.square_wave_stopped(
                    self._current_round().index,
                    success=False,
                    reason=self._failure_reason,
                )
            self._continue_after_square_wave_stop("faulted")

    @Slot(bool, str)
    def _on_square_wave_finished(self, success: bool, reason: str):
        if (
            not self.active
            or not self._square_wave_enabled
            or self._state is not ScanState.STOPPING_SIGNAL
        ):
            return
        next_action = self._after_square_wave_stop
        self._after_square_wave_stop = ""
        self._square_wave_active = False
        self._emit_scan_event(
            "motor_scan_square_wave_stopped",
            round_number=self._round_index + 1,
            success=bool(success),
            reason=str(reason),
        )
        if self._manifest_active:
            self._manifest.square_wave_stopped(
                self._current_round().index,
                success=bool(success),
                reason=str(reason),
            )
        if not success:
            self._failure_reason = (
                self._failure_reason
                or str(reason)
                or "方波停止确认失败，输出状态未知"
            )
            next_action = "faulted"
        self._continue_after_square_wave_stop(next_action)

    def _continue_after_square_wave_stop(self, action: str):
        if action == "return":
            self._begin_return()
        elif action == "stopped":
            if self._deferred_export_task_ids:
                self._start_deferred_exports("stopped")
            else:
                self._finish_stopped("user_stop")
        else:
            if self._deferred_export_task_ids:
                self._start_deferred_exports("faulted")
            else:
                self._finish_fault()

    def _begin_return(self):
        self._move_index = 0
        self._set_state(ScanState.RETURNING)
        if self._uses_process_path:
            path_id = (
                f"{self._scan_id}:round:{self._round_index + 1}:return"
            )
            self._active_path_id = path_id
            self._path_returning = True
            if not self.motor.start_scan_path(
                path_id,
                self._current_round().return_moves,
                returning=True,
            ):
                self._active_path_id = ""
                self._begin_fault("电机返回路径未被接受")
            return
        self._advance_return()

    def _finish_return_path(self):
        if self._manifest_active:
            self._manifest.return_finished(
                self._current_round().index,
                completed=True,
            )
        self._emit_scan_event(
            "motor_scan_round_completed",
            round_number=self._round_index + 1,
            round_total=len(self._plan.rounds),
            scan_move_total=len(self._current_round().scan_moves),
            return_move_total=len(self._current_round().return_moves),
        )
        self._round_index += 1
        if self._round_index >= len(self._plan.rounds):
            if self._deferred_export_task_ids:
                self._start_deferred_exports("completed")
            else:
                self._complete()
        else:
            self._start_round()

    @Slot(object)
    def _on_path_segment_started(self, payload):
        values = dict(payload)
        if values.get("path_id") != self._active_path_id:
            return
        self._move_index = max(0, int(values["segment_number"]) - 1)
        self._emit_scan_event(
            "motor_scan_segment_started",
            round_number=self._round_index + 1,
            **values,
        )
        self.progress_changed.emit(
            self._round_index + 1,
            len(self._plan.rounds),
            int(values["segment_number"]),
            int(values["segment_total"]),
        )

    @Slot(object)
    def _on_path_segment_finished(self, payload):
        values = dict(payload)
        if values.get("path_id") != self._active_path_id:
            return
        self._emit_scan_event(
            "motor_scan_segment_finished",
            round_number=self._round_index + 1,
            **values,
        )

    @Slot(str, bool, str)
    def _on_path_finished(self, path_id: str, success: bool, reason: str):
        if str(path_id) != self._active_path_id:
            return
        returning = self._path_returning
        self._active_path_id = ""
        self._path_returning = False
        if self._user_stopping:
            return
        if not success:
            self._begin_fault(str(reason) or "电机路径执行失败")
            return
        if returning:
            self._finish_return_path()
        else:
            self._verify_scan_round()

    @Slot(str, object, bool)
    def _on_scan_capture_sealed(self, task_id: str, files, failed: bool):
        task_id = str(task_id)
        if task_id != self._current_task_id:
            return
        round_index = self._current_round().index
        recovery_files = [str(path) for path in files]
        self._deferred_export_task_ids.append(task_id)
        self._deferred_task_rounds[task_id] = round_index
        self._current_task_id = ""
        reason = "光谱仪采集封存或完整性校验失败" if failed else ""
        if self._manifest_active:
            self._manifest.acquisition_sealed(
                round_index,
                recovery_files,
                failed=bool(failed),
                reason=reason,
            )
        self._emit_scan_event(
            "motor_scan_capture_sealed",
            round_number=round_index,
            acquisition_id=task_id,
            recovery_files=recovery_files,
            failed=bool(failed),
        )
        if self._user_stopping:
            self._request_square_wave_stop("stopped")
            return
        if failed or self._failure_reason:
            if failed and not self._failure_reason:
                self._failure_reason = reason
            if getattr(self.motor, "motion_active", False):
                self.motor.stop()
            self._request_square_wave_stop("faulted")
            return
        if self._state is not ScanState.STOPPING_ACQUISITION:
            self._begin_fault("光谱仪任务在非预期状态完成封存")
            return
        self._request_square_wave_stop("return")

    def _start_deferred_exports(self, finish_mode: str):
        if self._state is ScanState.EXPORTING:
            if finish_mode != "completed":
                self._finish_after_exports = str(finish_mode)
            return
        task_ids = tuple(self._deferred_export_task_ids)
        if not task_ids:
            if finish_mode == "completed":
                self._complete()
            elif finish_mode == "stopped":
                self._finish_stopped("user_stop")
            else:
                self._finish_fault()
            return
        self._finish_after_exports = str(finish_mode)
        self._pending_export_task_ids = set(task_ids)
        self._set_state(ScanState.EXPORTING)
        self._emit_scan_event(
            "motor_scan_exports_started",
            acquisition_ids=list(task_ids),
            export_total=len(task_ids),
        )
        if not hasattr(self.acquisition, "start_deferred_scan_exports") or not (
            self.acquisition.start_deferred_scan_exports(task_ids)
        ):
            self._failure_reason = "无法启动扫描光谱数据导出"
            self._finish_fault()

    @Slot(str, object, object)
    def _on_task_finished(self, task_id: str, files, failed):
        task_id = str(task_id)
        if task_id in self._deferred_task_rounds:
            round_index = self._deferred_task_rounds[task_id]
            result_files = [str(path) for path in files]
            reason = "光谱仪扫描数据导出失败" if bool(failed) else ""
            if self._manifest_active:
                self._manifest.acquisition_finished(
                    round_index,
                    result_files,
                    failed=bool(failed),
                    reason=reason,
                )
            self._emit_scan_event(
                "motor_scan_export_finished",
                round_number=round_index,
                acquisition_id=task_id,
                files=result_files,
                failed=bool(failed),
            )
            if failed and not self._export_failure_reason:
                self._export_failure_reason = reason
            self._pending_export_task_ids.discard(task_id)
            if (
                self._state is ScanState.EXPORTING
                and not self._pending_export_task_ids
            ):
                if self._export_failure_reason:
                    self._failure_reason = self._export_failure_reason
                    self._finish_fault()
                elif self._finish_after_exports == "completed":
                    self._complete()
                elif self._finish_after_exports == "stopped":
                    self._finish_stopped("user_stop")
                else:
                    self._finish_fault()
            return
        if task_id != self._current_task_id:
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
            self._request_square_wave_stop("stopped")
            return
        if acquisition_failed:
            if not self._failure_reason:
                self._failure_reason = reason
            self._request_square_wave_stop("faulted")
            return
        if self._state is not ScanState.STOPPING_ACQUISITION:
            self._begin_fault("光谱仪任务提前结束")
            return
        self._request_square_wave_stop("return")

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

    @Slot(bool, str)
    def _on_square_wave_connection_changed(
        self, connected: bool, detail: str
    ):
        if self.active and self._square_wave_enabled and not connected:
            self._begin_fault(
                str(detail) or "扫描过程中方波发生器连接断开"
            )

    def stop(self):
        if not self.active:
            return False
        self._user_stopping = True
        if self._state is ScanState.EXPORTING:
            self._finish_after_exports = "stopped"
            return True
        self._dwell_timer.stop()
        waiting_for_signal_start = (
            self._state is ScanState.STARTING_SIGNAL
            and not self._square_wave_active
        )
        if not waiting_for_signal_start and self._state is not ScanState.STOPPING_SIGNAL:
            self._set_state(ScanState.STOPPING)
        self._current_operation_id = ""
        self._current_move = None
        self._waiting_round_verification = False
        self._active_path_id = ""
        self._path_returning = False
        self.motor.stop()
        if self._current_task_id:
            if not self.acquisition.stop_global():
                self._request_square_wave_stop("stopped")
        elif waiting_for_signal_start:
            return True
        elif self._square_wave_active:
            self._request_square_wave_stop("stopped")
        elif self._deferred_export_task_ids:
            self._start_deferred_exports("stopped")
        else:
            self._finish_stopped("user_stop")
        return True

    def _begin_fault(self, reason: str):
        if self._finished_emitted:
            return
        self._failure_reason = str(reason) or "scan_fault"
        if self._state is ScanState.EXPORTING:
            self._finish_after_exports = "faulted"
            return
        self._dwell_timer.stop()
        waiting_for_signal_start = (
            self._state is ScanState.STARTING_SIGNAL
            and not self._square_wave_active
        )
        if not waiting_for_signal_start and self._state is not ScanState.STOPPING_SIGNAL:
            self._set_state(ScanState.STOPPING)
        self._current_operation_id = ""
        self._current_move = None
        self._waiting_round_verification = False
        self._active_path_id = ""
        self._path_returning = False
        if (
            getattr(self.motor, "motion_active", False)
            or getattr(self.motor, "scan_active", False)
        ):
            self.motor.stop()
        if self._current_task_id:
            if self.acquisition.stop_global():
                return
        if waiting_for_signal_start:
            return
        if self._square_wave_active:
            self._request_square_wave_stop("faulted")
            return
        if self._deferred_export_task_ids:
            self._start_deferred_exports("faulted")
            return
        self._finish_fault()

    def _finish_fault(self):
        if (
            self._manifest_active
            and self._plan is not None
            and self._round_index < len(self._plan.rounds)
        ):
            self._manifest.return_finished(
                self._current_round().index,
                completed=False,
                reason=self._failure_reason,
            )
        if self._manifest_active:
            self._manifest.finish("faulted", self._failure_reason)
        self._set_state(ScanState.FAULTED)
        self.operation_failed.emit(self._failure_reason)
        self._emit_finished(False, self._failure_reason)

    def _finish_stopped(self, reason: str):
        if (
            self._manifest_active
            and self._plan is not None
            and self._round_index < len(self._plan.rounds)
        ):
            self._manifest.return_finished(
                self._current_round().index,
                completed=False,
                reason=reason,
            )
        if self._manifest_active:
            self._manifest.finish("stopped", reason)
        self._set_state(ScanState.IDLE)
        self._emit_finished(False, reason)

    def _complete(self):
        if self._manifest_active:
            self._manifest.finish("completed")
        self._set_state(ScanState.COMPLETED)
        self._emit_finished(True, "completed")

    def _emit_finished(self, success: bool, reason: str):
        if self._finished_emitted:
            return
        if hasattr(self.motor, "set_scan_active"):
            self.motor.set_scan_active(False)
        self._finished_emitted = True
        self._emit_scan_event(
            "motor_scan_finished",
            status=(
                "completed"
                if success
                else "stopped" if reason == "user_stop" else "faulted"
            ),
            reason=str(reason),
            completed_rounds=min(
                self._round_index,
                len(self._plan.rounds) if self._plan is not None else 0,
            ),
        )
        self.scan_finished.emit(
            bool(success),
            str(reason),
            self.manifest_path,
        )
