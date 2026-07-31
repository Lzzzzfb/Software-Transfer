"""总控、单机和背景/参考共用的采集生命周期控制器。"""

from dataclasses import dataclass, field
from pathlib import Path
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
from ..storage.manual_capture import PendingManualCapture
from ..storage.spool_lifecycle import cleanup_spool_files


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
    process_spool_devices: set = field(default_factory=set)
    sealed_devices: set = field(default_factory=set)
    sealed_results: Dict[int, dict] = field(default_factory=dict)
    seal_requested: bool = False
    external_export_started: bool = False


class AcquisitionController(QtCore.QObject):
    """Serializes unsafe actions while allowing independent devices to run."""

    global_state_changed = Signal(object)
    device_state_changed = Signal(int, object)
    operation_rejected = Signal(str)
    diagnostic_event = Signal(str)
    task_started = Signal(str, object)
    task_finished = Signal(str, object, object)
    reference_captured = Signal(str, object)
    manual_capture_changed = Signal(object)
    manual_export_started = Signal(str)
    manual_export_finished = Signal(str, object, bool)

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
        self._pending_manual_capture = None
        self._manual_export_task_id = None

        self.device_manager.command_completed.connect(self._on_command_completed)
        if hasattr(self.device_manager, "device_updated"):
            self.device_manager.device_updated.connect(self._on_device_changed)
        if hasattr(self.device_manager, "device_removed"):
            self.device_manager.device_removed.connect(self._on_device_changed)
        if hasattr(self.device_manager, "persistent_session_sealed"):
            self.device_manager.persistent_session_sealed.connect(
                self._on_persistent_session_sealed
            )
        if hasattr(self.device_manager, "acquisition_stop_requested"):
            self.device_manager.acquisition_stop_requested.connect(
                self._on_acquisition_stop_requested
            )
        if storage_manager is not None:
            storage_manager.session_closed.connect(self._on_storage_closed)
            if hasattr(storage_manager, "controlled_stop_requested"):
                storage_manager.controlled_stop_requested.connect(
                    self._on_storage_stop_requested
                )
            if hasattr(storage_manager, "warning_event"):
                storage_manager.warning_event.connect(
                    lambda task_id, message: self.diagnostic_event.emit(message)
                )

    @property
    def global_state(self) -> ControlState:
        return self._global_state

    @property
    def global_task_id(self) -> Optional[str]:
        return self._global_task_id

    @property
    def busy(self) -> bool:
        return bool(self._tasks) or self.manual_export_active

    @property
    def pending_manual_capture(self):
        return self._pending_manual_capture

    @property
    def manual_export_active(self) -> bool:
        return self._manual_export_task_id is not None

    @property
    def can_save_pending_capture(self) -> bool:
        return (
            self._pending_manual_capture is not None
            and not self._tasks
            and not self.manual_export_active
        )

    def device_state(self, device_id: int) -> ControlState:
        return self._device_states.get(device_id, ControlState.IDLE)

    def active_task_for_device(self, device_id: int) -> Optional[AcquisitionRequest]:
        task = self._task_for_device(device_id)
        return task.request if task else None

    def can_modify_device(self, device_id: int) -> bool:
        return (
            self.device_state(device_id) is ControlState.IDLE
            and device_id not in self._hardware_busy_device_ids()
        )

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
        if self.manual_export_active:
            return self._reject("光谱正在保存，请等待完成后再开始采集")
        if self._global_task_id is not None:
            return self._reject("总控任务正在运行，请使用顶部停止")
        if self.device_state(device_id) is not ControlState.IDLE:
            return self._reject(self._busy_device_message([device_id]))
        if device_id in self._hardware_busy_device_ids():
            return self._reject(
                f"设备 {device_id} 仍处于下位机采集或启动状态，请先停止"
            )
        device = self._ready_device(device_id)
        if device is None:
            return self._reject(f"设备 {device_id} 未连接或尚未初始化")
        if not self.discard_pending_capture("new_acquisition"):
            return self._reject(
                "上一任务缓存无法清理，已取消新的采集"
            )
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
        return self._start_global(
            AcquisitionOwner.GLOBAL,
            device_ids,
            mode,
            sync_mode,
            master_device_id=master_device_id,
            auto_store=auto_store,
            storage_format=storage_format,
            batch_size=batch_size,
        )

    def start_scan_global(
        self,
        device_ids,
        mode=AcquisitionMode.CONTINUOUS,
        sync_mode=SyncMode.INDEPENDENT,
        *,
        master_device_id: Optional[int] = None,
        auto_store: bool = True,
        storage_format=StorageFormat.CSV_EXCEL,
        batch_size: int = 500,
    ) -> bool:
        return self._start_global(
            AcquisitionOwner.SCAN,
            device_ids,
            mode,
            sync_mode,
            master_device_id=master_device_id,
            auto_store=auto_store,
            storage_format=storage_format,
            batch_size=batch_size,
        )

    def _start_global(
        self,
        owner,
        device_ids,
        mode=AcquisitionMode.CONTINUOUS,
        sync_mode=SyncMode.INDEPENDENT,
        *,
        master_device_id: Optional[int] = None,
        auto_store: bool = False,
        storage_format=StorageFormat.CSV_EXCEL,
        batch_size: int = 500,
    ) -> bool:
        if self.manual_export_active:
            return self._reject("光谱正在保存，请等待完成后再开始采集")
        busy_ids = [
            device_id
            for device_id, state in self._device_states.items()
            if state is not ControlState.IDLE
        ]
        busy_ids = sorted(set(busy_ids) | self._hardware_busy_device_ids())
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
        if not self.discard_pending_capture("new_acquisition"):
            return self._reject(
                "上一任务缓存无法清理，已取消新的采集"
            )
        request = AcquisitionRequest.create(
            AcquisitionOwner(owner),
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
        if self.busy or self._hardware_busy_device_ids():
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
        if self.busy or self._hardware_busy_device_ids():
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
        if (
            task.request.auto_store
            and not task.process_spool_devices
            and self.storage_manager is not None
        ):
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

    def save_pending_capture(self) -> bool:
        if self._tasks:
            return self._reject("采集任务未完全结束，暂时不能保存光谱")
        if self.manual_export_active:
            return self._reject("光谱正在保存，请勿重复操作")
        pending = self._pending_manual_capture
        if pending is None:
            return self._reject("没有可保存的上一任务光谱")
        if self.storage_manager is None:
            return self._reject("存储服务不可用")
        try:
            self.storage_manager.start_prepared_export(
                pending.context,
                cleanup_sources=True,
            )
        except Exception as exc:
            self.diagnostic_event.emit(f"手动保存启动失败：{exc}")
            return self._reject("光谱保存启动失败，缓存已保留")
        self._manual_export_task_id = pending.task_id
        self.manual_export_started.emit(pending.task_id)
        self.diagnostic_event.emit("正在后台保存上一任务光谱")
        return True

    def discard_pending_capture(self, reason: str) -> bool:
        pending = self._pending_manual_capture
        if pending is None:
            return True
        if self.manual_export_active:
            self.diagnostic_event.emit("光谱正在保存，不能清理待保存缓存")
            return False
        cleanup = cleanup_spool_files(pending.spool_paths)
        for message in cleanup.warnings:
            self.diagnostic_event.emit(message)
        if cleanup.retained:
            self.diagnostic_event.emit(
                "上一任务缓存清理失败："
                + "；".join(str(path) for path in cleanup.retained)
            )
            return False
        self._pending_manual_capture = None
        self.manual_capture_changed.emit(None)
        if reason == "new_acquisition":
            self.diagnostic_event.emit(
                "上一任务未保存数据已被新采集覆盖"
            )
        elif reason == "shutdown":
            self.diagnostic_event.emit(
                "正常退出，未保存的上一任务缓存已清除"
            )
        return True

    def _replace_pending_capture(self, context) -> bool:
        if self._pending_manual_capture is not None:
            if not self.discard_pending_capture("replaced"):
                return False
        pending = PendingManualCapture.create(context)
        self._pending_manual_capture = pending
        self.manual_capture_changed.emit(pending)
        self.diagnostic_event.emit(
            f"上一任务已保留 {pending.frame_count} 帧，"
            "可点击“保存光谱”导出"
        )
        return True

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
        output_directory = (
            Path(self.storage_manager.output_directory)
            if self.storage_manager is not None
            else Path.cwd() / "data"
        )
        output_directory.mkdir(parents=True, exist_ok=True)
        for device_id in task.request.device_ids:
            device = self.device_manager.get_device(device_id)
            if device is None or device.port_name.startswith("SIM"):
                continue
            path = output_directory / (
                f"{task.request.task_id}_device_{device_id}.capture.part"
            )
            metadata = {
                "session_id": task.request.task_id,
                "device_id": device_id,
                "port": device.port_name,
                "started_at": task.request.started_at,
                "storage_format": task.request.storage_format.value,
                "auto_store": task.request.auto_store,
                "n_pixel": device.info.pixel_count,
                "start_pixel": device.info.start_pixel,
                "valid_pixel": device.info.valid_pixel,
            }
            try:
                if (
                    hasattr(self.device_manager, "begin_persistent_session")
                    and self.device_manager.begin_persistent_session(
                    device_id, path, metadata
                    )
                ):
                    task.process_spool_devices.add(device_id)
            except Exception as exc:
                self._release_task(task)
                return self._reject(f"无法启动独立采集持久化：{exc}")

        if task.request.auto_store and not task.process_spool_devices:
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
        if task.process_spool_devices and not task.seal_requested:
            task.seal_requested = True
            for device_id in task.process_spool_devices:
                try:
                    if not self.device_manager.end_persistent_session(device_id):
                        task.failed = True
                        task.sealed_devices.add(device_id)
                except Exception as exc:
                    task.failed = True
                    task.sealed_devices.add(device_id)
                    self.diagnostic_event.emit(
                        f"设备 {device_id} 采集缓存封存失败：{exc}"
                    )
            if task.sealed_devices != task.process_spool_devices:
                self.diagnostic_event.emit("设备已停止，正在封存全量采集数据")
                return
        self._finish_finalization(task)

    def _finish_finalization(self, task: _ActiveTask) -> None:
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
        if (
            task.request.auto_store
            and not task.process_spool_devices
            and self.storage_manager is not None
        ):
            try:
                self.diagnostic_event.emit("设备采集已停止，正在后台生成 CSV/Excel")
                self.storage_manager.close_session(task.request.task_id)
                return
            except Exception as exc:
                task.failed = True
                self.diagnostic_event.emit(f"存储收尾失败：{exc}")
        if (
            task.request.auto_store
            and task.process_spool_devices
            and self.storage_manager is not None
            and not task.external_export_started
        ):
            try:
                devices = [
                    self.device_manager.get_device(device_id)
                    for device_id in task.request.device_ids
                ]
                if any(device is None for device in devices):
                    raise RuntimeError("导出前设备信息不完整")
                task.external_export_started = True
                self.diagnostic_event.emit(
                    "全量采集数据已封存，正在后台生成 CSV/Excel"
                )
                self.storage_manager.start_sealed_export(
                    task.request,
                    devices,
                    task.sealed_results,
                    cleanup_sources=not task.failed,
                )
                return
            except Exception as exc:
                task.failed = True
                self.diagnostic_event.emit(f"封存数据导出启动失败：{exc}")
        files = self._existing_spool_files(task)
        if (
            task.process_spool_devices
            and not task.request.auto_store
            and not task.failed
        ):
            if task.request.owner is AcquisitionOwner.CALIBRATION:
                cleanup = cleanup_spool_files(files)
                for message in cleanup.warnings:
                    self.diagnostic_event.emit(message)
                files = [str(path) for path in cleanup.retained]
            else:
                counts = [
                    int(values.get("persisted_frames", 0))
                    for values in task.sealed_results.values()
                ]
                if counts and all(count == 0 for count in counts):
                    cleanup = cleanup_spool_files(files)
                    for message in cleanup.warnings:
                        self.diagnostic_event.emit(message)
                    files = [str(path) for path in cleanup.retained]
                    self.diagnostic_event.emit("本次采集没有可保存的数据")
                elif not counts or any(count <= 0 for count in counts):
                    task.failed = True
                    self.diagnostic_event.emit(
                        "各设备持久化帧数不完整，缓存已保留供恢复"
                    )
                elif self.storage_manager is None:
                    task.failed = True
                    self.diagnostic_event.emit(
                        "存储服务不可用，缓存已保留供恢复"
                    )
                else:
                    try:
                        devices = [
                            self.device_manager.get_device(device_id)
                            for device_id in task.request.device_ids
                        ]
                        if any(device is None for device in devices):
                            raise RuntimeError("待保存设备信息不完整")
                        context = self.storage_manager.prepare_sealed_export(
                            task.request,
                            devices,
                            task.sealed_results,
                        )
                        if self._replace_pending_capture(context):
                            files = []
                        else:
                            task.failed = True
                            self.diagnostic_event.emit(
                                "无法替换上一任务缓存，"
                                "本次缓存已保留供恢复"
                            )
                    except Exception as exc:
                        task.failed = True
                        self.diagnostic_event.emit(
                            f"待保存光谱准备失败：{exc}"
                        )
        self._release_task(task, files)

    @staticmethod
    def _existing_spool_files(task: _ActiveTask):
        return [
            str(path)
            for result in task.sealed_results.values()
            if (path := result.get("spool_path")) and Path(path).exists()
        ]

    def _on_persistent_session_sealed(self, device_id: int, result) -> None:
        task = self._task_for_device(device_id)
        if task is None or device_id not in task.process_spool_devices:
            return
        values = dict(result or {})
        task.sealed_devices.add(device_id)
        task.sealed_results[device_id] = values
        sealed = bool(values.get("sealed", False))
        complete = values.get("raw_complete_frames", 0)
        persisted = values.get("persisted_frames", 0)
        if not sealed:
            task.failed = True
            self.diagnostic_event.emit(
                f"设备 {device_id} 全量采集文件未完成封存，已保留 .part 文件供恢复"
            )
        elif complete != persisted:
            task.failed = True
            self.diagnostic_event.emit(
                f"设备 {device_id} 完整帧 {complete} 与持久化帧 {persisted} 不一致"
            )
        else:
            self.diagnostic_event.emit(
                f"设备 {device_id} 已封存 {persisted} 帧全量采集数据"
            )
        if (
            task.phase is ControlState.FINALIZING
            and task.sealed_devices == task.process_spool_devices
        ):
            self._finish_finalization(task)

    def _on_storage_closed(self, task_id: str, files, errors) -> None:
        if task_id == self._manual_export_task_id:
            self._manual_export_task_id = None
            failed = bool(errors)
            for error in errors:
                self.diagnostic_event.emit(f"手动保存错误：{error}")
            pending = self._pending_manual_capture
            if not failed:
                self._pending_manual_capture = None
                self.manual_capture_changed.emit(None)
            elif pending is not None and not any(
                path.exists() for path in pending.spool_paths
            ):
                self._pending_manual_capture = None
                self.manual_capture_changed.emit(None)
            self.manual_export_finished.emit(
                task_id, list(files), failed
            )
            return
        task = self._tasks.get(task_id)
        if task is None:
            return
        if errors:
            task.failed = True
            for error in errors:
                self.diagnostic_event.emit(f"存储错误：{error}")
        self._release_task(task, files)

    def _on_storage_stop_requested(self, task_id: str, message: str) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        task.failed = True
        self.diagnostic_event.emit(message)
        self._request_stop(task)

    def _on_acquisition_stop_requested(
        self, device_id: int, message: str
    ) -> None:
        task = self._task_for_device(device_id)
        if task is None:
            return
        task.failed = True
        self.diagnostic_event.emit(
            f"设备 {device_id} 请求受控停止：{message}"
        )
        self._request_stop(task)

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

    def _hardware_busy_device_ids(self) -> set:
        requested = set(
            getattr(self.device_manager, "_acquisition_requested", set())
        )
        devices = getattr(self.device_manager, "devices", {})
        busy = {
            int(device_id)
            for device_id, device in devices.items()
            if bool(getattr(device, "acquiring", False))
        }
        busy.update(int(device_id) for device_id in requested)
        return busy

    def _busy_device_message(self, device_ids) -> str:
        details = []
        hardware_busy = self._hardware_busy_device_ids()
        for device_id in device_ids:
            device = self.device_manager.get_device(device_id)
            name = device.port_name if device else f"设备 {device_id}"
            state = self.device_state(device_id)
            if state is ControlState.IDLE and device_id in hardware_busy:
                label = "下位机仍在采集或启动"
            else:
                label = control_state_label(state)
            details.append(f"{name}（{label}）")
        return "以下设备尚未完全空闲：" + "、".join(details)

    def _reject(self, message: str) -> bool:
        self.operation_rejected.emit(message)
        return False
