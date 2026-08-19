"""High-level square-wave connection, output and ownership controller."""

from __future__ import annotations

from dataclasses import dataclass, replace
import time
import uuid

from ..qt import QtCore, Signal, Slot
from .discovery import available_port_records, find_square_wave_candidates
from .models import (
    DEFAULT_BAUD_RATE,
    DeviceIdentity,
    DeviceStatus,
    OutputOwner,
    OutputState,
    PortCandidate,
    SquareWaveParameters,
)
from .protocol import (
    id_command,
    pulse_frequency_command,
    pulse_width_command,
    start_command,
    status_command,
    stop_command,
)
from .settings_store import HostSquareWaveSettings, SquareWaveSettingsStore
from .transport import SquareWaveSerialTransport


@dataclass(frozen=True)
class SquareWaveControllerState:
    connected: bool
    port_name: str
    identity: DeviceIdentity | None
    output_state: OutputState
    owner: OutputOwner
    parameters: SquareWaveParameters
    busy: bool
    detail: str = ""


class SquareWaveController(QtCore.QObject):
    candidates_changed = Signal(object)
    connection_changed = Signal(bool, str)
    status_changed = Signal(object)
    parameters_applied = Signal(bool, str)
    operation_failed = Signal(str)
    diagnostic_event = Signal(str)
    scan_round_prepared = Signal(bool, str)
    scan_round_finished = Signal(bool, str)

    def __init__(
        self,
        parent=None,
        *,
        transport=None,
        settings_store=None,
        port_provider=available_port_records,
    ):
        super().__init__(parent)
        self.transport = transport or SquareWaveSerialTransport(self)
        self.settings_store = settings_store or SquareWaveSettingsStore(
            "square-wave-settings.json"
        )
        self.host_settings = self.settings_store.load()
        self.port_provider = port_provider
        self._candidates: tuple[PortCandidate, ...] = ()
        self._probe_candidates: tuple[PortCandidate, ...] = ()
        self._probe_index = -1
        self._probing = False
        self._suppress_disconnect = False
        self._confirmed_candidate: PortCandidate | None = None
        self._connected = False
        self._identity: DeviceIdentity | None = None
        self._device_status: DeviceStatus | None = None
        self._output_state = OutputState.STOPPED
        self._owner = OutputOwner.NONE
        self._parameters = self.host_settings.parameters
        self._operation = ""
        self._operation_id = ""
        self._operation_started_at = 0.0
        self._desired_parameters: SquareWaveParameters | None = None
        self._desired_owner = OutputOwner.NONE
        self._disconnect_after_stop = False
        self._detail = ""

        self.transport.connection_changed.connect(
            self._on_transport_connection
        )
        self.transport.command_completed.connect(self._on_command_completed)
        self.transport.command_failed.connect(self._on_command_failed)
        self.transport.diagnostic_event.connect(self.diagnostic_event)

        if getattr(self.settings_store, "last_error", ""):
            self.diagnostic_event.emit(
                "方波设置读取失败，已使用默认值："
                f"{self.settings_store.last_error}"
            )

    @property
    def connected(self):
        return self._connected

    @property
    def port_name(self):
        if self._confirmed_candidate is None:
            return ""
        return (
            self._confirmed_candidate.system_location
            or self._confirmed_candidate.port_name
        )

    @property
    def identity(self):
        return self._identity

    @property
    def candidates(self):
        return self._candidates

    @property
    def serial_number(self):
        return (
            self._confirmed_candidate.serial_number
            if self._confirmed_candidate is not None
            else ""
        )

    @property
    def output_state(self):
        return self._output_state

    @property
    def owner(self):
        return self._owner

    @property
    def parameters(self):
        return self._parameters

    @property
    def busy(self):
        return bool(self._probing or self._operation)

    @property
    def state(self):
        return SquareWaveControllerState(
            connected=self.connected,
            port_name=self.port_name,
            identity=self.identity,
            output_state=self.output_state,
            owner=self.owner,
            parameters=self.parameters,
            busy=self.busy,
            detail=self._detail,
        )

    def _emit_status(self, detail=""):
        if detail:
            self._detail = str(detail)
        self.status_changed.emit(self.state)

    def update_host_settings(self, settings):
        if not isinstance(settings, HostSquareWaveSettings):
            raise TypeError("invalid square-wave host settings")
        if self.busy or self._output_state is OutputState.RUNNING:
            self.operation_failed.emit("方波任务正在运行，不能修改连接设置")
            return False
        self.host_settings = settings
        self.settings_store.save(settings)
        self._parameters = settings.parameters
        self._emit_status("方波上位机设置已保存")
        return True

    def discover(self, excluded_ports=frozenset()):
        self._candidates = find_square_wave_candidates(
            self.port_provider(),
            excluded_ports=excluded_ports,
            preferred_serial=self.host_settings.usb_serial,
            preferred_port=(
                self.host_settings.system_location
                or self.host_settings.port_name
            ),
        )
        self.candidates_changed.emit(self._candidates)
        self.diagnostic_event.emit(
            f"方波串口候选数：{len(self._candidates)}；身份确认只发送 ID?"
        )
        return self._candidates

    def connect_auto(self, excluded_ports=frozenset()):
        candidates = self.discover(excluded_ports)
        if not candidates:
            self._report_failure("未发现可探测的方波发生器串口")
            self.connection_changed.emit(False, "未找到方波发生器")
            return False
        return self._begin_probe(candidates)

    def connect_manual(self, port_name: str):
        port_name = str(port_name).strip()
        if not port_name:
            self._report_failure("手动串口不能为空")
            return False
        records = find_square_wave_candidates(self.port_provider())
        normalized = port_name.replace("\\", "/").casefold()
        candidate = next(
            (
                item
                for item in records
                if normalized
                in {
                    item.port_name.replace("\\", "/").casefold(),
                    item.system_location.replace("\\", "/").casefold(),
                }
            ),
            PortCandidate(port_name, port_name),
        )
        self.host_settings = replace(
            self.host_settings,
            automatic_port=False,
            port_name=port_name,
        )
        return self._begin_probe((candidate,))

    def _begin_probe(self, candidates):
        if self.busy:
            self._report_failure("方波控制器正在执行其他操作")
            return False
        if self._connected:
            self._close_transport("重新识别方波发生器")
        self._probe_candidates = tuple(candidates)
        self._probe_index = -1
        self._probing = True
        self._connected = False
        self._identity = None
        self._confirmed_candidate = None
        self._advance_candidate()
        self._emit_status("正在识别方波发生器串口")
        return True

    def _advance_candidate(self, reason=""):
        previous = (
            self._probe_candidates[self._probe_index]
            if 0 <= self._probe_index < len(self._probe_candidates)
            else None
        )
        if previous is not None and reason:
            self.diagnostic_event.emit(
                f"方波候选 {previous.port_name} 未通过身份校验：{reason}"
            )
        self._suppress_disconnect = True
        try:
            self.transport.disconnect_port()
        finally:
            self._suppress_disconnect = False
        self._probe_index += 1
        if self._probe_index >= len(self._probe_candidates):
            self._probing = False
            self._report_failure("候选串口均未通过方波发生器身份校验")
            self.connection_changed.emit(False, "未找到方波发生器")
            self._emit_status("方波识别完成：未找到设备")
            return
        candidate = self._probe_candidates[self._probe_index]
        self.transport.connect_port(candidate.port_name, DEFAULT_BAUD_RATE)

    @Slot(bool, str)
    def _on_transport_connection(self, connected: bool, detail: str):
        if self._suppress_disconnect:
            return
        if connected and self._probing:
            self._send(
                id_command(),
                expected="identity",
                tag=("probe", "identity"),
                read_retries=1,
            )
            return
        if not connected and self._probing:
            self._advance_candidate(detail or "open_failed")
            return
        if not connected and self._connected:
            was_unsafe = (
                self._output_state is not OutputState.STOPPED
                or bool(self._operation)
            )
            self._connected = False
            self._owner = OutputOwner.NONE
            if was_unsafe:
                self._output_state = OutputState.UNKNOWN
            self._fail_active_operation(
                detail or "串口断开",
                force_unknown=was_unsafe,
            )
            if was_unsafe:
                self._report_failure(
                    "方波串口断开，输出状态未知；重连后将先执行停止确认"
                )
            self.connection_changed.emit(False, detail or "串口断开")
            self._emit_status(detail or "方波串口断开")

    def _send(
        self,
        request,
        *,
        expected,
        tag,
        action=False,
        read_retries=0,
        timeout_ms=500,
    ):
        return self.transport.send_request(
            request,
            expected=expected,
            tag=tag,
            timeout_ms=timeout_ms,
            read_retries=read_retries,
            action=action,
        )

    @Slot(object, object)
    def _on_command_completed(self, tag, response):
        if not isinstance(tag, tuple) or len(tag) < 2:
            return
        kind, step = tag[0], tag[1]
        if kind == "probe":
            self._continue_probe(step, response)
            return
        if kind != "operation" or not self._operation:
            return
        if step == "apply_frequency":
            self._send_apply_width()
        elif step == "apply_width":
            self._send(
                status_command(),
                expected="status",
                tag=("operation", "apply_verify", self._operation_id),
                read_retries=1,
            )
        elif step == "apply_verify":
            self._complete_apply_verification(response)
        elif step == "start":
            self._send(
                status_command(),
                expected="status",
                tag=("operation", "start_verify", self._operation_id),
                read_retries=1,
            )
        elif step == "start_verify":
            self._complete_start_verification(response)
        elif step == "stop":
            self._send(
                status_command(),
                expected="status",
                tag=("operation", "stop_verify", self._operation_id),
                read_retries=1,
            )
        elif step == "stop_verify":
            self._complete_stop_verification(response)
        elif step == "query_status":
            if isinstance(response, DeviceStatus):
                self._apply_device_status(response)
                self._complete_operation(True, "方波状态已读取")

    def _continue_probe(self, step, response):
        if not self._probing:
            return
        if step == "identity":
            if not isinstance(response, DeviceIdentity) or not response.supported:
                if isinstance(response, DeviceIdentity):
                    detail = (
                        f"unsupported identity: {response.name} "
                        f"protocol={response.protocol_version}"
                    )
                else:
                    detail = "invalid identity response"
                self._advance_candidate(detail)
                return
            self._identity = response
            self._send(
                status_command(),
                expected="status",
                tag=("probe", "status"),
                read_retries=1,
            )
            return
        if not isinstance(response, DeviceStatus):
            self._advance_candidate("invalid status response")
            return
        self._apply_device_status(response)
        if response.running:
            self._operation = "connect_reconcile"
            self._start_operation_timer()
            self._send(
                stop_command(),
                expected="ok",
                tag=("operation", "stop", self._operation_id),
                action=True,
            )
            return
        self._finalize_connection()

    def _finalize_connection(self):
        candidate = self._probe_candidates[self._probe_index]
        self._probing = False
        self._connected = True
        self._confirmed_candidate = candidate
        self._owner = OutputOwner.NONE
        self._output_state = OutputState.STOPPED
        self.host_settings = replace(
            self.host_settings,
            port_name=candidate.port_name,
            system_location=candidate.system_location,
            usb_serial=candidate.serial_number,
        )
        self.settings_store.save(self.host_settings)
        self.connection_changed.emit(True, candidate.port_name)
        self.diagnostic_event.emit(
            "方波发生器已连接："
            f"{candidate.system_location or candidate.port_name}；"
            f"{self._parameters.frequency_hz} Hz；"
            f"{self._parameters.pulse_width_us} us；输出已确认关闭"
        )
        self._emit_status("方波发生器已连接")

    @Slot(object, str)
    def _on_command_failed(self, tag, reason: str):
        if not isinstance(tag, tuple) or len(tag) < 2:
            return
        kind = tag[0]
        if kind == "probe" and self._probing:
            self._advance_candidate(f"{tag[1]}: {reason}")
            return
        if kind != "operation":
            return
        force_unknown = (
            str(reason) == "result_unknown"
            or tag[1] in {
                "apply_frequency",
                "apply_width",
                "apply_verify",
                "start",
                "start_verify",
                "stop",
                "stop_verify",
            }
        )
        self._fail_active_operation(str(reason), force_unknown=force_unknown)

    def apply_parameters(self, parameters):
        if not isinstance(parameters, SquareWaveParameters):
            raise TypeError("invalid square-wave parameters")
        if not self._can_begin_idle_operation("应用方波参数"):
            return False
        self._operation = "apply"
        self._desired_parameters = parameters
        self._start_operation_timer()
        self._send_apply_frequency()
        self._emit_status("正在应用方波参数")
        return True

    def _send_apply_frequency(self):
        self._send(
            pulse_frequency_command(self._desired_parameters.frequency_hz),
            expected="ok",
            tag=("operation", "apply_frequency", self._operation_id),
            action=True,
        )

    def _send_apply_width(self):
        self._send(
            pulse_width_command(self._desired_parameters.pulse_width_us),
            expected="ok",
            tag=("operation", "apply_width", self._operation_id),
            action=True,
        )

    def _complete_apply_verification(self, response):
        desired = self._desired_parameters
        if not isinstance(response, DeviceStatus):
            self._fail_active_operation("参数状态回读无效", force_unknown=True)
            return
        self._apply_device_status(response)
        if response.running or response.parameters != desired:
            self._fail_active_operation("方波参数回读不一致")
            return
        if self._operation == "scan_prepare":
            self._send(
                start_command(),
                expected="ok",
                tag=("operation", "start", self._operation_id),
                action=True,
            )
            return
        self.host_settings = replace(
            self.host_settings,
            parameters=desired,
        )
        self.settings_store.save(self.host_settings)
        self.parameters_applied.emit(True, "方波参数已写入并回读确认")
        self._complete_operation(True, "方波参数已应用")

    def start_output(self):
        if not self._can_begin_idle_operation("启动方波输出"):
            return False
        self._operation = "manual_start"
        self._desired_owner = OutputOwner.MANUAL
        self._start_operation_timer()
        self._send(
            start_command(),
            expected="ok",
            tag=("operation", "start", self._operation_id),
            action=True,
        )
        self._emit_status("正在启动方波输出")
        return True

    def _complete_start_verification(self, response):
        if not isinstance(response, DeviceStatus):
            self._fail_active_operation("启动状态回读无效", force_unknown=True)
            return
        self._apply_device_status(response)
        if not response.running:
            self._fail_active_operation("设备未报告正在输出")
            return
        if self._operation == "scan_prepare":
            self._owner = OutputOwner.SCAN
            self.host_settings = replace(
                self.host_settings,
                parameters=response.parameters,
            )
            self.settings_store.save(self.host_settings)
            self._complete_operation(True, "扫描方波已启动")
            self.scan_round_prepared.emit(True, "方波输出已确认开启")
            return
        self._owner = self._desired_owner
        self._complete_operation(True, "方波输出已启动")

    def stop_output(self):
        if not self._connected:
            self._report_failure("方波发生器未连接")
            return False
        if self.busy:
            self._report_failure("方波控制器正在执行其他操作")
            return False
        if self._output_state is OutputState.STOPPED:
            self._owner = OutputOwner.NONE
            self._emit_status("方波输出已关闭")
            return True
        self._begin_stop("manual_stop")
        return True

    def _begin_stop(self, operation):
        self._operation = str(operation)
        self._start_operation_timer()
        self._send(
            stop_command(),
            expected="ok",
            tag=("operation", "stop", self._operation_id),
            action=True,
        )
        self._emit_status("正在停止方波输出")

    def _complete_stop_verification(self, response):
        if not isinstance(response, DeviceStatus):
            self._fail_active_operation("停止状态回读无效", force_unknown=True)
            return
        self._apply_device_status(response)
        if response.running:
            self._fail_active_operation("设备仍报告正在输出")
            return
        operation = self._operation
        self._owner = OutputOwner.NONE
        if operation == "connect_reconcile":
            self._clear_operation()
            self._finalize_connection()
            return
        if operation == "scan_stop":
            self._complete_operation(True, "扫描方波已停止")
            self.scan_round_finished.emit(True, "方波输出已确认关闭")
            return
        disconnect_after = self._disconnect_after_stop
        self._disconnect_after_stop = False
        self._complete_operation(True, "方波输出已停止")
        if disconnect_after:
            self._close_transport("方波发生器已断开")

    def prepare_scan_round(self, parameters):
        if not isinstance(parameters, SquareWaveParameters):
            raise TypeError("invalid square-wave parameters")
        if not self._connected:
            return self._reject_scan_prepare("方波发生器未连接")
        if self.busy:
            return self._reject_scan_prepare("方波控制器正在执行其他操作")
        if self._owner is OutputOwner.MANUAL:
            return self._reject_scan_prepare("方波正由手动操作占用")
        if self._owner is not OutputOwner.NONE:
            return self._reject_scan_prepare("方波已被扫描任务占用")
        if self._output_state is OutputState.UNKNOWN:
            return self._reject_scan_prepare(
                "方波输出状态未知，请重新连接并完成停止确认"
            )
        if self._output_state is not OutputState.STOPPED:
            return self._reject_scan_prepare("方波输出未停止")
        self._operation = "scan_prepare"
        self._desired_parameters = parameters
        self._desired_owner = OutputOwner.SCAN
        self._start_operation_timer()
        self._send_apply_frequency()
        self._emit_status("正在准备扫描方波")
        return True

    def _reject_scan_prepare(self, reason):
        self._report_failure(reason)
        self.scan_round_prepared.emit(False, str(reason))
        return False

    def finish_scan_round(self):
        if not self._connected:
            self._report_failure("方波发生器未连接")
            self.scan_round_finished.emit(False, "方波发生器未连接")
            return False
        if self.busy:
            self._report_failure("方波控制器正在执行其他操作")
            return False
        if self._owner is not OutputOwner.SCAN:
            self._report_failure("当前没有方波扫描所有权")
            self.scan_round_finished.emit(False, "当前没有方波扫描所有权")
            return False
        self._begin_stop("scan_stop")
        return True

    def query_status(self):
        if not self._connected or self.busy:
            return False
        self._operation = "query_status"
        self._start_operation_timer()
        self._send(
            status_command(),
            expected="status",
            tag=("operation", "query_status", self._operation_id),
            read_retries=1,
        )
        return True

    def disconnect(self):
        if not self._connected and not self._probing:
            return True
        if self._probing:
            self._probing = False
            self._close_transport("方波识别已取消")
            return True
        if self.busy:
            self._report_failure("方波控制器正在执行其他操作")
            return False
        if self._output_state is OutputState.STOPPED:
            self._close_transport("方波发生器已断开")
            return True
        self._disconnect_after_stop = True
        self._begin_stop("disconnect_stop")
        return True

    def _close_transport(self, detail):
        self._suppress_disconnect = True
        try:
            self.transport.disconnect_port()
        finally:
            self._suppress_disconnect = False
        self._connected = False
        self._owner = OutputOwner.NONE
        self.connection_changed.emit(False, str(detail))
        self._emit_status(detail)

    def _can_begin_idle_operation(self, label):
        if not self._connected:
            self._report_failure("方波发生器未连接")
            return False
        if self.busy:
            self._report_failure("方波控制器正在执行其他操作")
            return False
        if self._owner is not OutputOwner.NONE:
            self._report_failure(f"{label}失败：方波已被其他任务占用")
            return False
        if self._output_state is not OutputState.STOPPED:
            self._report_failure(f"{label}失败：方波输出未确认关闭")
            return False
        return True

    def _apply_device_status(self, status):
        self._device_status = status
        self._parameters = status.parameters
        self._output_state = status.output_state
        self._emit_status()

    def _start_operation_timer(self):
        self._operation_id = uuid.uuid4().hex[:8]
        self._operation_started_at = time.monotonic()

    def _complete_operation(self, success, detail):
        operation = self._operation
        elapsed_ms = max(
            0,
            round((time.monotonic() - self._operation_started_at) * 1000),
        )
        self.diagnostic_event.emit(
            f"方波操作 op={operation} result={'ok' if success else 'failed'} "
            f"elapsed_ms={elapsed_ms} detail={detail}"
        )
        self._clear_operation()
        self._emit_status(detail)

    def _clear_operation(self):
        self._operation = ""
        self._operation_id = ""
        self._operation_started_at = 0.0
        self._desired_parameters = None
        self._desired_owner = OutputOwner.NONE

    def _fail_active_operation(self, reason, *, force_unknown=False):
        operation = self._operation
        if not operation:
            return
        if force_unknown:
            self._output_state = OutputState.UNKNOWN
        message = f"方波操作失败：{reason}"
        self._complete_operation(False, message)
        self.operation_failed.emit(message)
        if operation == "apply":
            self.parameters_applied.emit(False, message)
        elif operation == "scan_prepare":
            self._owner = OutputOwner.NONE
            self.scan_round_prepared.emit(False, message)
        elif operation == "scan_stop":
            self.scan_round_finished.emit(False, message)

    def _report_failure(self, reason):
        self.operation_failed.emit(str(reason))
        self.diagnostic_event.emit(f"方波操作拒绝：{reason}")

    def shutdown(self):
        if self._connected and self._output_state is not OutputState.STOPPED:
            self.diagnostic_event.emit(
                "应用关闭时方波输出未确认停止，尝试发送 STOP；"
                "若未收到确认则输出状态未知"
            )
            self._send(
                stop_command(),
                expected="ok",
                tag=("shutdown", "stop"),
                action=True,
                timeout_ms=300,
            )
            self._output_state = OutputState.UNKNOWN
        self._connected = False
        self._owner = OutputOwner.NONE
        self.transport.shutdown()
