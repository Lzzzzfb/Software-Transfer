"""总控、单机和背景/参考共用的采集生命周期控制器。"""

from dataclasses import dataclass, field
import time
from typing import Callable, Dict, Optional

from ..communication.protocol import CmdCode, TriggerMode
from ..domain.enums import (
    AcquisitionMode,
    AcquisitionOwner,
    ControlState,
    StorageFormat,
    SyncMode,
    control_state_label,
)
from ..domain.models import AcquisitionRequest, SpectrumFrame
from ..qt import QtCore, Signal


@dataclass
class _ActiveTask:
    request: AcquisitionRequest
    global_scope: bool
    master_device_id: Optional[int] = None
    desired_trigger_modes: Dict[int, int] = field(default_factory=dict)
    configured: set = field(default_factory=set)
    start_targets: set = field(default_factory=set)
    started: set = field(default_factory=set)
    stop_targets: set = field(default_factory=set)
    stopped: set = field(default_factory=set)
    captured_frames: Dict[int, SpectrumFrame] = field(default_factory=dict)
    phase: ControlState = ControlState.IDLE
    failed: bool = False
    stop_requested_at: float = 0.0
    last_frame_at: float = 0.0


class AcquisitionController(QtCore.QObject):
    """Serializes unsafe actions while allowing independent devices to run."""

    global_state_changed = Signal(object)
    device_state_changed = Signal(int, object)
    operation_rejected = Signal(str)
    diagnostic_event = Signal(str)
    task_started = Signal(str, object)
    task_finished = Signal(str, object, object)
    reference_captured = Signal(str, object)

    def __init__(
        self,
        device_manager,
        storage_manager=None,
        reference_commit: Optional[Callable] = None,
        *,
        command_timeout_ms: int = 2500,
        frame_timeout_ms: int = 5000,
        tail_quiet_ms: int = 250,
        tail_max_ms: int = 2000,
        parent=None,
    ):
        super().__init__(parent)
        self.device_manager = device_manager
        self.storage_manager = storage_manager
        self.reference_commit = reference_commit
        self.command_timeout_ms = max(1, int(command_timeout_ms))
        self.frame_timeout_ms = max(1, int(frame_timeout_ms))
        self.tail_quiet_ms = max(0, int(tail_quiet_ms))
        self.tail_max_ms = max(self.tail_quiet_ms, int(tail_max_ms))
        self._tasks: Dict[str, _ActiveTask] = {}
        self._device_tasks: Dict[int, str] = {}
        self._device_states: Dict[int, ControlState] = {}
        self._expected_commands: Dict[int, tuple] = {}
        self._global_task_id: Optional[str] = None
        self._global_state = ControlState.IDLE

        self.device_manager.command_completed.connect(self._on_command_completed)
        if hasattr(self.device_manager, "device_updated"):
            self.device_manager.device_updated.connect(self._on_device_changed)
        if hasattr(self.device_manager, "device_removed"):
            self.device_manager.device_removed.connect(self._on_device_changed)
        if storage_manager is not None:
            storage_manager.session_closed.connect(self._on_storage_closed)

    @property
    def global_state(self) -> ControlState:
        return self._global_state

    @property
    def busy(self) -> bool:
        return bool(self._tasks)

    def device_state(self, device_id: int) -> ControlState:
        return self._device_states.get(device_id, ControlState.IDLE)

    def active_task_for_device(self, device_id: int) -> Optional[AcquisitionRequest]:
        task = self._task_for_device(device_id)
        return task.request if task else None

    def can_modify_device(self, device_id: int) -> bool:
        return self.device_state(device_id) is ControlState.IDLE

    def can_remove_device(self, device_id: int) -> bool:
        return self.can_modify_device(device_id) and self._global_task_id is None

    def start_local(
        self,
        device_id: int,
        mode=AcquisitionMode.CONTINUOUS,
        *,
        auto_store: bool = False,
        storage_format=StorageFormat.CSV_EXCEL,
        batch_size: int = 500,
    ) -> bool:
        if self._global_task_id is not None:
            return self._reject("总控任务正在运行，请使用顶部停止")
        if self.device_state(device_id) is not ControlState.IDLE:
            return self._reject(self._busy_device_message([device_id]))
        device = self._ready_device(device_id)
        if device is None:
            return self._reject(f"设备 {device_id} 未连接或尚未初始化")
        request = AcquisitionRequest.create(
            AcquisitionOwner.LOCAL,
            [device_id],
            mode=AcquisitionMode(mode),
            sync_mode=SyncMode.INDEPENDENT,
            auto_store=bool(auto_store),
            storage_format=StorageFormat(storage_format),
            batch_size=batch_size,
        )
        task = _ActiveTask(request=request, global_scope=False)
        if not self._claim_task(task):
            return False
        task.desired_trigger_modes = {device_id: int(TriggerMode.SOFTWARE)}
        return self._begin_configuration(task)

    def start_global(
        self,
        device_ids,
        mode=AcquisitionMode.CONTINUOUS,
        sync_mode=SyncMode.INDEPENDENT,
        *,
        master_device_id: Optional[int] = None,
        auto_store: bool = False,
        storage_format=StorageFormat.CSV_EXCEL,
        batch_size: int = 500,
    ) -> bool:
        busy_ids = [
            device_id
            for device_id, state in self._device_states.items()
            if state is not ControlState.IDLE
        ]
        if self._global_task_id is not None or busy_ids:
            return self._reject(
                self._busy_device_message(busy_ids)
                if busy_ids
                else "总控任务已经在运行"
            )
        ids = tuple(int(device_id) for device_id in device_ids)
        if not ids:
            return self._reject("没有参与总控的可用设备")
        if any(self._ready_device(device_id) is None for device_id in ids):
            return self._reject("总控设备中存在未连接或未初始化的设备")
        sync = SyncMode(sync_mode)
        if sync is SyncMode.HARD_INTERNAL and master_device_id not in ids:
            return self._reject("内部硬同步需要选择参与总控的主设备")
        request = AcquisitionRequest.create(
            AcquisitionOwner.GLOBAL,
            ids,
            mode=AcquisitionMode(mode),
            sync_mode=sync,
            auto_store=bool(auto_store),
            storage_format=StorageFormat(storage_format),
            batch_size=batch_size,
        )
        task = _ActiveTask(
            request=request,
            global_scope=True,
            master_device_id=master_device_id,
        )
        task.desired_trigger_modes = self._global_trigger_modes(task)
        if not self._claim_task(task):
            return False
        return self._begin_configuration(task)

    def stop_local(self, device_id: int) -> bool:
        task = self._task_for_device(device_id)
        if task is None:
            return self._reject(f"设备 {device_id} 当前未采集")
        if task.global_scope:
            return self._reject("总控采集正在运行，请使用顶部停止")
        if task.request.owner is not AcquisitionOwner.LOCAL:
            return self._reject("背景或参考采集正在运行，请等待完成")
        return self._request_stop(task)

    def stop_global(self) -> bool:
        task = self._tasks.get(self._global_task_id or "")
        if task is None:
            return self._reject("总控当前未采集")
        return self._request_stop(task)

    def capture_local_reference(self, device_id: int, kind: str) -> bool:
        if self.busy:
            return self._reject("系统正在采集、停止或保存，请完全停止后再采集背景或参考")
        device = self._ready_device(device_id)
        if device is None:
            return self._reject(f"设备 {device_id} 未连接或尚未初始化")
        request = AcquisitionRequest.create(
            AcquisitionOwner.CALIBRATION,
            [device_id],
            mode=AcquisitionMode.SINGLE,
            sync_mode=SyncMode.INDEPENDENT,
            reference_kind=kind,
        )
        task = _ActiveTask(request=request, global_scope=False)
        task.desired_trigger_modes = {device_id: int(TriggerMode.SOFTWARE)}
        if not self._claim_task(task):
            return False
        return self._begin_configuration(task)

    def capture_global_reference(
        self,
        device_ids,
        kind: str,
        sync_mode=SyncMode.INDEPENDENT,
        *,
        master_device_id: Optional[int] = None,
    ) -> bool:
        if self.busy:
            return self._reject("系统正在采集、停止或保存，请完全停止后再采集背景或参考")
        ids = tuple(int(device_id) for device_id in device_ids)
        if not ids or any(self._ready_device(device_id) is None for device_id in ids):
            return self._reject("没有可执行总控背景或参考的完整设备集合")
        sync = SyncMode(sync_mode)
        if sync is SyncMode.HARD_INTERNAL and master_device_id not in ids:
            return self._reject("内部硬同步需要选择参与总控的主设备")
        request = AcquisitionRequest.create(
            AcquisitionOwner.CALIBRATION,
            ids,
            mode=AcquisitionMode.SINGLE,
            sync_mode=sync,
            reference_kind=kind,
        )
        task = _ActiveTask(
            request=request,
            global_scope=True,
            master_device_id=master_device_id,
        )
        task.desired_trigger_modes = self._global_trigger_modes(task)
        if not self._claim_task(task):
            return False
        return self._begin_configuration(task)

    def on_frame(self, frame: SpectrumFrame) -> None:
        task = self._task_for_device(frame.device_id)
        if task is None or task.phase not in (
            ControlState.ACQUIRING,
            ControlState.STOPPING,
        ):
            return
        task.last_frame_at = time.monotonic()
        if task.request.auto_store and self.storage_manager is not None:
            try:
                self.storage_manager.submit(frame)
            except Exception as exc:
                self.diagnostic_event.emit(f"存储提交失败：{exc}")
                task.failed = True
                self._request_stop(task)
                return
        if task.phase is not ControlState.ACQUIRING:
            return
        if task.request.mode is AcquisitionMode.SINGLE:
            task.captured_frames.setdefault(frame.device_id, frame)
            if set(task.captured_frames) == set(task.request.device_ids):
                self._request_stop(task)

    def finish_pending_stops(self) -> None:
        """Force quiet-stop completion; useful for shutdown and deterministic tests."""
        for task in list(self._tasks.values()):
            if task.phase is ControlState.STOPPING:
                self._finalize_task(task)

    def _claim_task(self, task: _ActiveTask) -> bool:
        for device_id in task.request.device_ids:
            if self.device_state(device_id) is not ControlState.IDLE:
                return self._reject(self._busy_device_message([device_id]))
        if task.global_scope:
            if self._global_task_id is not None:
                return self._reject("总控任务已经在运行")
            self._global_task_id = task.request.task_id
        self._tasks[task.request.task_id] = task
        for device_id in task.request.device_ids:
            self._device_tasks[device_id] = task.request.task_id
        if task.request.auto_store:
            if self.storage_manager is None:
                self._release_task(task)
                return self._reject("自动存储服务不可用")
            try:
                devices = [self.device_manager.get_device(item) for item in task.request.device_ids]
                self.storage_manager.start_session(task.request, devices)
            except Exception as exc:
                self._release_task(task)
                return self._reject(f"无法启动批量存储：{exc}")
        return True

    def _begin_configuration(self, task: _ActiveTask) -> bool:
        self._set_task_phase(task, ControlState.CONFIGURING)
        for device_id, trigger_mode in task.desired_trigger_modes.items():
            self._send_expected(
                task,
                device_id,
                CmdCode.SET_TRIG_MODE,
                lambda did=device_id, mode=trigger_mode:
                    self.device_manager.set_trigger_mode(did, mode),
            )
        return True

    def _begin_start(self, task: _ActiveTask) -> None:
        order = list(task.request.device_ids)
        if (
            task.request.sync_mode is SyncMode.HARD_INTERNAL
            and task.master_device_id in order
        ):
            order = [item for item in order if item != task.master_device_id]
            order.append(task.master_device_id)
        task.start_targets = set(order)
        self._set_task_phase(task, ControlState.STARTING)
        continuous = task.request.mode is AcquisitionMode.CONTINUOUS
        command = CmdCode.START_CONTINUOUS if continuous else CmdCode.START_SINGLE
        for device_id in order:
            self._send_expected(
                task,
                device_id,
                command,
                lambda did=device_id, value=continuous:
                    self.device_manager.start_acquisition(did, value),
            )

    def _request_stop(self, task: _ActiveTask) -> bool:
        if task.phase in (ControlState.STOPPING, ControlState.FINALIZING):
            return False
        task.phase = ControlState.STOPPING
        task.stop_requested_at = time.monotonic()
        task.last_frame_at = task.stop_requested_at
        task.stop_targets = set(task.request.device_ids)
        self._set_task_phase(task, ControlState.STOPPING)
        for device_id in task.request.device_ids:
            device = self.device_manager.get_device(device_id)
            if device is None or not device.connected:
                task.stopped.add(device_id)
                continue
            self._send_expected(
                task,
                device_id,
                CmdCode.STOP_ACQUISITION,
                lambda did=device_id: self.device_manager.stop_acquisition(did),
            )
        if task.stopped == task.stop_targets:
            self._schedule_quiet_finish(task)
        return True

    def _send_expected(self, task, device_id, command, sender) -> None:
        command_value = int(command)
        self._expected_commands[device_id] = (task.request.task_id, command_value)
        try:
            sent = bool(sender())
        except Exception as exc:
            self.diagnostic_event.emit(
                f"设备 {device_id} 命令 0x{command_value:02X} 发送异常：{exc}"
            )
            sent = False
        if not sent:
            self._on_expected_result(task, device_id, command_value, False)
            return
        QtCore.QTimer.singleShot(
            self.command_timeout_ms,
            lambda tid=task.request.task_id, did=device_id, cmd=command_value:
                self._on_command_timeout(tid, did, cmd),
        )

    def _on_command_completed(self, device_id, command, success, params) -> None:
        expected = self._expected_commands.get(device_id)
        command = int(command)
        if not expected or expected[1] != command:
            return
        task = self._tasks.get(expected[0])
        if task is None:
            self._expected_commands.pop(device_id, None)
            return
        self._on_expected_result(task, device_id, command, bool(success))

    def _on_command_timeout(self, task_id: str, device_id: int, command: int) -> None:
        if self._expected_commands.get(device_id) != (task_id, command):
            return
        task = self._tasks.get(task_id)
        if task is None:
            return
        self.diagnostic_event.emit(
            f"设备 {device_id} 命令 0x{command:02X} ACK 超时"
        )
        self._on_expected_result(task, device_id, command, False)

    def _on_expected_result(
        self, task: _ActiveTask, device_id: int, command: int, success: bool
    ) -> None:
        if self._expected_commands.get(device_id) == (task.request.task_id, command):
            self._expected_commands.pop(device_id, None)
        if command == int(CmdCode.SET_TRIG_MODE):
            if not success:
                self._abort_task(task, f"设备 {device_id} 触发模式配置失败")
                return
            task.configured.add(device_id)
            if task.configured == set(task.request.device_ids):
                self._begin_start(task)
            return
        if command in (int(CmdCode.START_SINGLE), int(CmdCode.START_CONTINUOUS)):
            if not success:
                self._abort_task(task, f"设备 {device_id} 启动失败")
                return
            task.started.add(device_id)
            self._set_device_state(device_id, ControlState.ACQUIRING)
            if task.started == task.start_targets:
                task.phase = ControlState.ACQUIRING
                if task.global_scope:
                    self._set_global_state(ControlState.ACQUIRING)
                self.task_started.emit(task.request.task_id, task.request)
                if task.request.mode is AcquisitionMode.SINGLE:
                    QtCore.QTimer.singleShot(
                        self.frame_timeout_ms,
                        lambda tid=task.request.task_id: self._on_frame_timeout(tid),
                    )
            return
        if command == int(CmdCode.STOP_ACQUISITION):
            if not success:
                task.failed = True
                self.diagnostic_event.emit(f"设备 {device_id} 停止 ACK 失败或超时")
            task.stopped.add(device_id)
            if task.stopped == task.stop_targets:
                self._schedule_quiet_finish(task)

    def _on_frame_timeout(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task is None or task.phase is not ControlState.ACQUIRING:
            return
        missing = sorted(set(task.request.device_ids) - set(task.captured_frames))
        if not missing:
            return
        task.failed = True
        self.diagnostic_event.emit(
            "单次采集等待数据超时，未完成设备："
            + ", ".join(str(item) for item in missing)
        )
        self._request_stop(task)

    def _on_device_changed(self, device_id: int) -> None:
        task = self._task_for_device(device_id)
        if task is None:
            return
        device = self.device_manager.get_device(device_id)
        if device is not None and device.connected:
            return
        task.failed = True
        expected = self._expected_commands.get(device_id)
        if expected and expected[0] == task.request.task_id:
            self._expected_commands.pop(device_id, None)
        self.diagnostic_event.emit(f"设备 {device_id} 在采集任务中断开")
        if task.phase is ControlState.STOPPING:
            task.stopped.add(device_id)
            if task.stopped == task.stop_targets:
                self._schedule_quiet_finish(task)
            return
        self._request_stop(task)

    def _abort_task(self, task: _ActiveTask, message: str) -> None:
        task.failed = True
        self.diagnostic_event.emit(message)
        if task.start_targets or task.started:
            self._request_stop(task)
        else:
            self._finalize_task(task)

    def _schedule_quiet_finish(self, task: _ActiveTask) -> None:
        if self.tail_quiet_ms <= 0:
            self._finalize_task(task)
            return
        QtCore.QTimer.singleShot(
            self.tail_quiet_ms,
            lambda tid=task.request.task_id: self._finish_if_quiet(tid),
        )

    def _finish_if_quiet(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task is None or task.phase is not ControlState.STOPPING:
            return
        now = time.monotonic()
        quiet_ms = (now - task.last_frame_at) * 1000
        total_ms = (now - task.stop_requested_at) * 1000
        if quiet_ms >= self.tail_quiet_ms or total_ms >= self.tail_max_ms:
            self._finalize_task(task)
            return
        delay = max(1, int(self.tail_quiet_ms - quiet_ms))
        QtCore.QTimer.singleShot(delay, lambda tid=task_id: self._finish_if_quiet(tid))

    def _finalize_task(self, task: _ActiveTask) -> None:
        if task.request.task_id not in self._tasks:
            return
        self._set_task_phase(task, ControlState.FINALIZING)
        if (
            task.request.owner is AcquisitionOwner.CALIBRATION
            and not task.failed
            and set(task.captured_frames) == set(task.request.device_ids)
        ):
            try:
                if self.reference_commit is not None:
                    self.reference_commit(
                        task.request.reference_kind, dict(task.captured_frames)
                    )
                self.reference_captured.emit(
                    task.request.reference_kind, dict(task.captured_frames)
                )
            except Exception as exc:
                task.failed = True
                self.diagnostic_event.emit(f"背景或参考提交失败：{exc}")
        if task.request.auto_store and self.storage_manager is not None:
            try:
                self.storage_manager.close_session(task.request.task_id)
                return
            except Exception as exc:
                task.failed = True
                self.diagnostic_event.emit(f"存储收尾失败：{exc}")
        self._release_task(task)

    def _on_storage_closed(self, task_id: str, files, errors) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        if errors:
            task.failed = True
            for error in errors:
                self.diagnostic_event.emit(f"存储错误：{error}")
        self._release_task(task, files)

    def _release_task(self, task: _ActiveTask, files=None) -> None:
        task_id = task.request.task_id
        if task_id not in self._tasks:
            return
        for device_id in task.request.device_ids:
            if self._device_tasks.get(device_id) == task_id:
                self._device_tasks.pop(device_id, None)
            expected = self._expected_commands.get(device_id)
            if expected and expected[0] == task_id:
                self._expected_commands.pop(device_id, None)
            self._set_device_state(device_id, ControlState.IDLE)
        if self._global_task_id == task_id:
            self._global_task_id = None
            self._set_global_state(ControlState.IDLE)
        self._tasks.pop(task_id, None)
        self.task_finished.emit(task_id, files or [], task.failed)

    def _global_trigger_modes(self, task: _ActiveTask) -> Dict[int, int]:
        if task.request.sync_mode is SyncMode.HARD_EXTERNAL:
            return {
                device_id: int(TriggerMode.EXTERNAL)
                for device_id in task.request.device_ids
            }
        if task.request.sync_mode is SyncMode.HARD_INTERNAL:
            return {
                device_id: int(
                    TriggerMode.SOFT_MASTER
                    if device_id == task.master_device_id
                    else TriggerMode.EXTERNAL
                )
                for device_id in task.request.device_ids
            }
        return {
            device_id: int(TriggerMode.SOFTWARE)
            for device_id in task.request.device_ids
        }

    def _set_task_phase(self, task: _ActiveTask, state: ControlState) -> None:
        task.phase = state
        for device_id in task.request.device_ids:
            self._set_device_state(device_id, state)
        if task.global_scope:
            self._set_global_state(state)

    def _set_device_state(self, device_id: int, state: ControlState) -> None:
        if self.device_state(device_id) is state:
            return
        self._device_states[device_id] = state
        self.device_state_changed.emit(device_id, state)

    def _set_global_state(self, state: ControlState) -> None:
        if self._global_state is state:
            return
        self._global_state = state
        self.global_state_changed.emit(state)

    def _task_for_device(self, device_id: int) -> Optional[_ActiveTask]:
        return self._tasks.get(self._device_tasks.get(device_id, ""))

    def _ready_device(self, device_id: int):
        device = self.device_manager.get_device(device_id)
        if device and device.connected and device.initialized:
            return device
        return None

    def _busy_device_message(self, device_ids) -> str:
        details = []
        for device_id in device_ids:
            device = self.device_manager.get_device(device_id)
            name = device.port_name if device else f"设备 {device_id}"
            details.append(f"{name}（{control_state_label(self.device_state(device_id))}）")
        return "以下设备尚未完全空闲：" + "、".join(details)

    def _reject(self, message: str) -> bool:
        self.operation_rejected.emit(message)
        return False
