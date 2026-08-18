"""High-level LK-MD2202 discovery, coordinate and motion controller."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
import time
import uuid

from ..qt import QtCore, Signal, Slot
from .discovery import MotorPortCandidate, available_port_records, find_motor_candidates
from .lk_md2202 import (
    AxisConfiguration,
    BAUD_RATE_TO_CODE,
    DEFAULT_ADDRESS,
    DEFAULT_BAUD_RATE,
    CommunicationConfiguration,
    DeviceConfiguration,
    DriverAxis,
    decode_axis_configuration,
    decode_axis_status,
    decode_communication,
    decode_supported_identity,
    axis_configuration_request,
    absolute_move_request,
    communication_request,
    configuration_changes,
    home_request,
    identity_request,
    position_speed_request,
    relative_move_request,
    status_request,
    stop_request,
    velocity_mode_request,
)
from .models import (
    MOTOR_PULSES_PER_MM,
    MOTOR_SPEED_DEFAULT_HZ,
    MOTOR_SPEED_MAX_HZ,
    MOTOR_SPEED_MIN_HZ,
    MOTOR_TRAVEL_MM,
    Axis,
    Direction,
    MotorAxisStatus,
    MotorStatus,
    home_timeout_seconds,
    stall_release_timeout_seconds,
)
from .modbus_rtu import build_write_single
from .settings_store import HostMotorSettings, MotorSettingsStore
from .scan_timing import MotionHandoffEstimator
from .transport import MotorSerialTransport


RELEASE_STOP_CONFIRM_SECONDS = 1.0
RELEASE_STOP_POLL_MS = 100
RELEASE_STOP_MAX_CHECKS = 4
MOTION_STATUS_TIMEOUT_MS = 100
MOTION_STATUS_REPOLL_MS = 20
MOTION_STATUS_READ_RETRIES = 0
SCAN_ACK_TIMEOUT_MS = 60
SCAN_ACCEPTANCE_SECONDS = 1.0
SCAN_STATUS_TIMEOUT_MS = 150
SCAN_STATUS_REPOLL_MS = 40
SCAN_VERIFY_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True)
class _ActiveMotion:
    operation_id: str
    kind: str
    axis: Axis
    start_pulses: int | None
    target_pulses: int | None
    deadline: float
    remaining_home_axes: tuple[Axis, ...] = ()
    initial_limit_active: bool | None = None
    limit_transition_seen: bool = False
    signed_speed_pps: int = 0
    stop_reason: str = ""
    stop_success: bool = False
    stop_check_count: int = 0
    fallback_stop_sent: bool = False
    operation_kind: str = ""
    started_at: float = 0.0
    status_query_count: int = 0
    status_failure_count: int = 0
    predictive: bool = False
    acceptance_deadline: float = 0.0
    acceptance_retry_count: int = 0
    command_accepted: bool = False
    estimator: MotionHandoffEstimator | None = None
    handoff_deadline: float = 0.0
    scan_returning: bool = False


def _unknown_axis(axis):
    return MotorAxisStatus(Axis(axis), None, None, False, False, None)


def _unknown_status():
    return MotorStatus(_unknown_axis(Axis.X), _unknown_axis(Axis.Y))


class MotorController(QtCore.QObject):
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

    def __init__(self, parent=None, *, transport=None, settings_store=None, port_provider=available_port_records):
        super().__init__(parent)
        self.transport = transport or MotorSerialTransport(self)
        self.settings_store = settings_store or MotorSettingsStore("motor-settings.json")
        self.host_settings = self.settings_store.load()
        self.port_provider = port_provider
        self._candidates = ()
        self._probe_candidates = ()
        self._probe_profiles = ()
        self._probe_index = -1
        self._probe_original_host = self.host_settings
        self._probing = False
        self._confirmed_candidate: MotorPortCandidate | None = None
        self._connected = False
        self._status = _unknown_status()
        self._axis_device_status = {Axis.X: None, Axis.Y: None}
        self._axis_limit_known = {Axis.X: False, Axis.Y: False}
        self._calibrated = {Axis.X: False, Axis.Y: False}
        self._software_zero = {Axis.X: 0, Axis.Y: 0}
        self._speeds = {Axis.X: MOTOR_SPEED_DEFAULT_HZ, Axis.Y: MOTOR_SPEED_DEFAULT_HZ}
        self._active_motion: _ActiveMotion | None = None
        self._configuration: DeviceConfiguration | None = None
        self._pending_configuration = {}
        self._initialization_step = ""
        self._scan_active = False
        self._scan_round_baseline = None
        self._scan_round_planned = None
        self._scan_prepare_values = None
        self._scan_verify_values = None
        self._scan_verify_deadline = 0.0
        self._config_ops = []
        self._desired_configuration = None
        self._desired_host_settings = None
        self._pre_apply_host_settings = None
        self._configuration_result_unknown = ""
        self._config_reconnect_pending = False
        self._safety_locked = False
        self._safety_lock_reason = ""
        self._safety_status_pending = False
        self._emergency_pending = 0

        self._motion_poll_timer = QtCore.QTimer(self)
        self._motion_poll_timer.setSingleShot(True)
        self._motion_poll_timer.timeout.connect(self._poll_motion)
        self._idle_poll_timer = QtCore.QTimer(self)
        self._idle_poll_timer.setInterval(500)
        self._idle_poll_timer.timeout.connect(self._poll_idle_status)
        self._scan_aux_timer = QtCore.QTimer(self)
        self._scan_aux_timer.setSingleShot(True)
        self._scan_aux_timer.timeout.connect(self._continue_scan_verification)
        self.transport.connection_changed.connect(self._on_transport_connection)
        self.transport.command_completed.connect(self._on_command_completed)
        self.transport.command_failed.connect(self._on_command_failed)
        self.transport.diagnostic_event.connect(self.diagnostic_event)

    @property
    def connected(self):
        return self._connected

    @property
    def device_id(self):
        return self._confirmed_candidate.stable_id if self._confirmed_candidate else ""

    @property
    def status(self):
        return self._status

    @property
    def motion_active(self):
        return self._active_motion is not None

    @property
    def safety_locked(self):
        return self._safety_locked

    @property
    def configuration(self):
        return self._configuration

    @property
    def mechanics_valid(self):
        return bool(
            self._configuration
            and self._configuration.x.matches_confirmed_mechanics
            and self._configuration.y.matches_confirmed_mechanics
        )

    @property
    def scan_active(self):
        return self._scan_active

    def set_scan_active(self, active: bool):
        if active and self._safety_locked:
            self.operation_failed.emit(
                self._safety_lock_reason or "电机停止状态未知，不能启动扫描"
            )
            return False
        self._scan_active = bool(active)
        if self._scan_active:
            self._idle_poll_timer.stop()
        else:
            self._scan_round_baseline = None
            self._scan_round_planned = None
            self._scan_prepare_values = None
            self._scan_verify_values = None
            self._scan_aux_timer.stop()
            if self._connected:
                self._idle_poll_timer.start()
        return True

    def update_host_settings(self, settings):
        """Persist connection/direction preferences without writing the driver."""
        if not isinstance(settings, HostMotorSettings):
            raise TypeError("invalid motor host settings")
        if self.motion_active or self._scan_active:
            self.configuration_apply_finished.emit(False, "电机任务正在运行")
            return False
        self.host_settings = settings
        self.settings_store.save(settings)
        self._refresh_status()
        self.configuration_apply_finished.emit(
            True, "上位机连接和方向设置已保存；驱动板未连接，未写入设备参数"
        )
        return True

    def discover(self, excluded_ports=frozenset()):
        self._candidates = find_motor_candidates(
            self.port_provider(), excluded_ports=excluded_ports, preferred_port=self.host_settings.port_name
        )
        self.candidates_changed.emit(self._candidates)
        self.diagnostic_event.emit(
            f"电机串口候选数：{len(self._candidates)}；自动识别仅执行只读请求"
        )
        return self._candidates

    def connect_auto(self, excluded_ports=frozenset()):
        candidates = self.discover(excluded_ports)
        if not candidates:
            self.operation_failed.emit("未发现可探测的电机串口")
            self.connection_changed.emit(False, "未找到LK-MD2202电机驱动板")
            return False
        self._probe_original_host = self.host_settings
        pairs = []
        for candidate in candidates:
            pairs.append((candidate, self.host_settings.address, self.host_settings.baud_rate))
            if (self.host_settings.address, self.host_settings.baud_rate) != (
                DEFAULT_ADDRESS,
                DEFAULT_BAUD_RATE,
            ):
                pairs.append((candidate, DEFAULT_ADDRESS, DEFAULT_BAUD_RATE))
        self._probe_candidates = tuple(item[0] for item in pairs)
        self._probe_profiles = tuple((item[1], item[2]) for item in pairs)
        self._probe_index = -1
        self._probing = True
        self._connected = False
        self._try_next_candidate()
        return True

    def connect_manual(self, port_name: str, address: int, baud_rate: int):
        self.host_settings = replace(
            self.host_settings, automatic_port=False, port_name=str(port_name), address=int(address), baud_rate=int(baud_rate)
        )
        self.settings_store.save(self.host_settings)
        self._probe_original_host = self.host_settings
        self._probe_candidates = (MotorPortCandidate(str(port_name), str(port_name), preferred=True),)
        self._probe_profiles = ((int(address), int(baud_rate)),)
        self._probe_index = -1
        self._probing = True
        self._try_next_candidate()
        return True

    def _try_next_candidate(self):
        self.transport.disconnect_port()
        self._probe_index += 1
        if self._probe_index >= len(self._probe_candidates):
            self._probing = False
            self.host_settings = self._probe_original_host
            self.operation_failed.emit("候选串口均未通过 LK-MD2202 只读身份校验")
            self.connection_changed.emit(False, "未找到LK-MD2202电机驱动板")
            if self._desired_configuration is not None:
                self._desired_configuration = None
                self._desired_host_settings = None
                self._pre_apply_host_settings = None
                self._config_ops.clear()
                self.configuration_apply_finished.emit(
                    False, "通信参数修改后的新旧参数均无法重新识别设备"
                )
            return
        candidate = self._probe_candidates[self._probe_index]
        address, baud_rate = self._probe_profiles[self._probe_index]
        self.host_settings = replace(
            self.host_settings, address=address, baud_rate=baud_rate
        )
        self.transport.connect_port(candidate.port_name, baud_rate)

    @Slot(bool, str)
    def _on_transport_connection(self, connected: bool, detail: str):
        if connected and self._config_reconnect_pending:
            self.transport.send_request(
                identity_request(self.host_settings.address),
                tag=("config_reconnect",),
                timeout_ms=700,
                read_retries=1,
            )
            return
        if not connected and self._config_reconnect_pending:
            self._fail_configuration_apply(
                f"使用新通信参数重新打开串口失败：{detail}"
            )
            return
        if connected and self._probing:
            self.transport.send_request(
                identity_request(self.host_settings.address), tag=("probe",), timeout_ms=500, read_retries=1
            )
            return
        if not connected and self._probing:
            self._try_next_candidate()
            return
        if not connected and self._connected:
            active = self._active_motion
            self._connected = False
            self._active_motion = None
            self._motion_poll_timer.stop()
            self._idle_poll_timer.stop()
            if active is not None:
                detail = "电机通信中断，停止状态未知，请切断驱动板电源"
                self._enter_safety_lock(detail)
                self._emit_motion_summary(
                    active, False, "connection_lost_stop_unconfirmed"
                )
                self.motion_finished.emit(
                    active.operation_id, False, "connection_lost_stop_unconfirmed"
                )
            self._calibrated = {Axis.X: False, Axis.Y: False}
            self._axis_limit_known = {Axis.X: False, Axis.Y: False}
            self._refresh_status()
            self.connection_changed.emit(False, detail)

    def _start_initialization(self):
        self._initialization_step = "x_config"
        self.transport.send_request(
            axis_configuration_request(self.host_settings.address, DriverAxis.X), tag=("init", "x_config"), read_retries=2
        )

    def _continue_initialization(self, step, response):
        if step == "x_config":
            self._pending_configuration[Axis.X] = decode_axis_configuration(response.registers)
            next_step, request = "y_config", axis_configuration_request(self.host_settings.address, DriverAxis.Y)
        elif step == "y_config":
            self._pending_configuration[Axis.Y] = decode_axis_configuration(response.registers)
            next_step, request = "communication", communication_request(self.host_settings.address)
        elif step == "communication":
            communication = decode_communication(response.registers)
            self._configuration = DeviceConfiguration(
                self._pending_configuration[Axis.X], self._pending_configuration[Axis.Y], communication
            )
            self._speeds[Axis.X] = self._configuration.x.position_speed_pps
            self._speeds[Axis.Y] = self._configuration.y.position_speed_pps
            self.configuration_changed.emit(self._configuration)
            self.diagnostic_event.emit(
                "LK-MD2202 配置已读取："
                f"地址 {communication.address}，{communication.baud_rate} baud，"
                f"X/M1 {self._configuration.x.end_position_pulses} pulse，"
                f"Y/M2 {self._configuration.y.end_position_pulses} pulse"
            )
            next_step, request = "x_status", status_request(self.host_settings.address, DriverAxis.X)
        elif step == "x_status":
            self._axis_device_status[Axis.X] = decode_axis_status(response.registers)
            self._axis_limit_known[Axis.X] = True
            next_step, request = "y_status", status_request(self.host_settings.address, DriverAxis.Y)
        else:
            self._axis_device_status[Axis.Y] = decode_axis_status(response.registers)
            self._axis_limit_known[Axis.Y] = True
            self._initialization_step = ""
            self._connected = True
            self._refresh_status()
            candidate = self._probe_candidates[self._probe_index]
            self._confirmed_candidate = replace(candidate, confirmed=True)
            self.host_settings = replace(self.host_settings, port_name=candidate.system_location or candidate.port_name)
            if self._desired_configuration is None:
                self.settings_store.save(self.host_settings)
            self.connection_changed.emit(True, candidate.port_name)
            self._idle_poll_timer.start()
            if self._safety_locked or any(
                raw is not None and raw.moving
                for raw in self._axis_device_status.values()
            ):
                self._start_emergency_stop("reconnect_stop_verification")
            if self._desired_configuration is not None:
                desired = self._desired_configuration
                success = self._configuration == desired
                unknown = self._configuration_result_unknown
                if success and self._desired_host_settings is not None:
                    self.host_settings = self._desired_host_settings
                    self.settings_store.save(self.host_settings)
                elif not success and self._pre_apply_host_settings is not None:
                    recovery = self._pre_apply_host_settings
                    communication = self._configuration.communication
                    recovery = replace(
                        recovery,
                        address=communication.address,
                        baud_rate=communication.baud_rate,
                        port_name=self.host_settings.port_name,
                    )
                    self.host_settings = recovery
                    self.settings_store.save(recovery)
                self._desired_configuration = None
                self._desired_host_settings = None
                self._pre_apply_host_settings = None
                self._configuration_result_unknown = ""
                self._refresh_status()
                self.configuration_apply_finished.emit(
                    success,
                    (
                        f"{unknown} 写入应答超时，但回读与目标值一致"
                        if success and unknown
                        else "配置已写入并回读一致"
                        if success
                        else f"{unknown} 写入结果未知，且配置回读与目标值不一致"
                        if unknown
                        else "配置回读与目标值不一致"
                    ),
                )
            return
        self._initialization_step = next_step
        self.transport.send_request(request, tag=("init", next_step), read_retries=2)

    def disconnect(self):
        self._probing = False
        self._connected = False
        self._active_motion = None
        self._motion_poll_timer.stop()
        self._idle_poll_timer.stop()
        self._confirmed_candidate = None
        self._calibrated = {Axis.X: False, Axis.Y: False}
        self._axis_device_status = {Axis.X: None, Axis.Y: None}
        self._axis_limit_known = {Axis.X: False, Axis.Y: False}
        self._status = _unknown_status()
        self.transport.disconnect_port()
        self.connection_changed.emit(False, "")
        self.status_changed.emit(self._status)

    def _reverse(self, axis):
        return self.host_settings.reverse_x if Axis(axis) is Axis.X else self.host_settings.reverse_y

    def _axis_status(self, axis):
        return self._status.x if Axis(axis) is Axis.X else self._status.y

    def _refresh_status(self):
        statuses = []
        for axis in (Axis.X, Axis.Y):
            raw = self._axis_device_status[axis]
            if raw is None:
                statuses.append(_unknown_axis(axis))
                continue
            logical_pulses = -raw.position_pulses if self._reverse(axis) else raw.position_pulses
            software_mm = (logical_pulses - self._software_zero[axis]) / MOTOR_PULSES_PER_MM
            statuses.append(MotorAxisStatus(
                axis,
                raw.position_pulses,
                software_mm,
                self._calibrated[axis],
                raw.moving,
                raw.limit_active if self._axis_limit_known[axis] else None,
                "LIMIT" if raw.limit_active and not raw.moving else "DONE" if not raw.moving else "MOVING",
                logical_pulses / MOTOR_PULSES_PER_MM if self._calibrated[axis] else None,
            ))
        self._status = MotorStatus(
            *statuses,
            fault_latched=self._safety_locked,
            fault_reason=self._safety_lock_reason,
        )
        self.status_changed.emit(self._status)

    def clear_software_zero(self, axis):
        axis = Axis(axis)
        if self.motion_active or self._scan_active or self._safety_locked:
            self.operation_failed.emit("运动或扫描期间不能修改软件零点")
            return False
        raw = self._axis_device_status[axis]
        if raw is None:
            self.operation_failed.emit(f"{axis.value} 轴位置不可用")
            return False
        logical = -raw.position_pulses if self._reverse(axis) else raw.position_pulses
        self._software_zero[axis] = logical
        self._refresh_status()
        return True

    def set_position(self, axis, position_mm):
        if float(position_mm) != 0:
            self.operation_failed.emit("LK-MD2202 软件坐标只支持清零")
            return False
        return self.clear_software_zero(axis)

    def set_speed(self, axis, speed_hz):
        axis = Axis(axis)
        signed_speed = int(speed_hz)
        absolute_speed = abs(signed_speed)
        if not MOTOR_SPEED_MIN_HZ <= absolute_speed <= MOTOR_SPEED_MAX_HZ:
            self.operation_failed.emit("速度绝对值必须在100～14000 pps之间，且不能为0")
            return False
        if (
            not self._connected
            or self.motion_active
            or self._scan_active
            or self._safety_locked
        ):
            self.operation_failed.emit("电机未连接或任务正在运行")
            return False
        return self.transport.send_request(
            position_speed_request(
                self.host_settings.address,
                DriverAxis(axis.motor_number),
                absolute_speed,
            ),
            tag=("speed", axis, signed_speed, absolute_speed),
            action=True,
        )

    def apply_configuration(self, desired, host_settings):
        if not isinstance(desired, DeviceConfiguration) or not isinstance(host_settings, HostMotorSettings):
            raise TypeError("invalid motor configuration")
        if (
            not self._connected
            or self.motion_active
            or self._scan_active
            or self._safety_locked
            or self._configuration is None
        ):
            self.configuration_apply_finished.emit(False, "电机未连接或任务正在运行")
            return False
        if not (
            desired.x.matches_confirmed_mechanics
            and desired.y.matches_confirmed_mechanics
        ):
            self.configuration_apply_finished.emit(
                False,
                "步距角、细分和终点位置必须保持 1.8° / 8 / 4800 pulse，"
                "以维持 320 pulse/mm 换算",
            )
            return False
        current = self._configuration
        operations = []
        for axis, old, new in (
            (Axis.X, current.x, desired.x),
            (Axis.Y, current.y, desired.y),
        ):
            for field, request in configuration_changes(
                current.communication.address, DriverAxis(axis.motor_number), old, new
            ):
                operations.append((f"{axis.value}.{field}", request))
        old_comm, new_comm = current.communication, desired.communication
        if old_comm.address != new_comm.address:
            operations.append((
                "communication.address",
                build_write_single(old_comm.address, 0x0050, new_comm.address),
            ))
        if old_comm.baud_rate != new_comm.baud_rate:
            operations.append((
                "communication.baud_rate",
                build_write_single(new_comm.address, 0x0051, BAUD_RATE_TO_CODE[new_comm.baud_rate]),
            ))
        self._desired_configuration = desired
        self._pre_apply_host_settings = self.host_settings
        self._configuration_result_unknown = ""
        self._desired_host_settings = replace(
            host_settings,
            address=new_comm.address,
            baud_rate=new_comm.baud_rate,
            port_name=(
                host_settings.port_name
                if not host_settings.automatic_port and host_settings.port_name
                else self.host_settings.port_name
            ),
        )
        self._config_ops = operations
        if not operations:
            self.host_settings = self._desired_host_settings
            self.settings_store.save(self.host_settings)
            self._desired_configuration = None
            self._desired_host_settings = None
            self._pre_apply_host_settings = None
            self._refresh_status()
            self.configuration_apply_finished.emit(True, "配置没有变化")
            return True
        self._send_next_config_op()
        return True

    def _send_next_config_op(self):
        if not self._config_ops:
            desired_host = self._desired_host_settings
            communication_changed = desired_host and (
                desired_host.address != self.host_settings.address
                or desired_host.baud_rate != self.host_settings.baud_rate
            )
            if desired_host is not None:
                self.host_settings = desired_host
            if communication_changed:
                port = self._confirmed_candidate.port_name if self._confirmed_candidate else desired_host.port_name
                self._connected = False
                self.transport.disconnect_port()
                candidate = MotorPortCandidate(port, port, preferred=True)
                previous = self._pre_apply_host_settings
                self._probe_candidates = (candidate, candidate)
                self._probe_profiles = (
                    (desired_host.address, desired_host.baud_rate),
                    (previous.address, previous.baud_rate),
                )
                self._probe_original_host = previous
                self._probe_index = -1
                self._probing = True
                self._try_next_candidate()
            else:
                self._pending_configuration.clear()
                self._start_initialization()
            return
        label, request = self._config_ops.pop(0)
        self.diagnostic_event.emit(f"正在写入电机配置字段：{label}")
        self.transport.send_request(request, tag=("config_write", label), timeout_ms=1000, action=True)

    def _reconnect_config_port(self, address, baud_rate):
        port = (
            self._confirmed_candidate.port_name
            if self._confirmed_candidate is not None
            else self.host_settings.port_name
        )
        self._connected = False
        self._config_reconnect_pending = True
        self.host_settings = replace(
            self.host_settings, address=int(address), baud_rate=int(baud_rate)
        )
        self.transport.disconnect_port()
        self.transport.connect_port(port, int(baud_rate))

    def _fail_configuration_apply(self, message):
        self._config_reconnect_pending = False
        self._config_ops.clear()
        if self._pre_apply_host_settings is not None:
            self.host_settings = self._pre_apply_host_settings
            self.settings_store.save(self.host_settings)
        self._desired_configuration = None
        self._desired_host_settings = None
        self._pre_apply_host_settings = None
        self.configuration_apply_finished.emit(False, str(message))

    def move_relative(self, axis, distance_mm, direction):
        axis, direction = Axis(axis), Direction(direction)
        distance_mm = float(distance_mm)
        if (
            not math.isfinite(distance_mm)
            or distance_mm <= 0
            or distance_mm > MOTOR_TRAVEL_MM
        ):
            self.operation_failed.emit("移动距离必须大于 0 且不超过 15 mm")
            return None
        pulses = round(distance_mm * MOTOR_PULSES_PER_MM) * direction.sign
        return self._start_logical_move(axis, pulses)

    def prepare_scan_round(self):
        """Read both axes and freeze an idempotent raw-position baseline."""
        if (
            not self._connected
            or not self._scan_active
            or self.motion_active
            or self._safety_locked
            or not self.mechanics_valid
            or self._scan_prepare_values is not None
            or self._scan_verify_values is not None
        ):
            return False
        self._scan_prepare_values = {}
        accepted = self.transport.send_request(
            status_request(self.host_settings.address, DriverAxis.X),
            tag=("scan_prepare", Axis.X),
            timeout_ms=SCAN_STATUS_TIMEOUT_MS,
            read_retries=0,
        )
        if not accepted:
            self._scan_prepare_values = None
        return bool(accepted)

    def _finish_scan_preparation(self, success, reason):
        values = self._scan_prepare_values
        if values is None:
            return
        self._scan_prepare_values = None
        if success and values is not None:
            baseline = {
                Axis.X: values[Axis.X].position_pulses,
                Axis.Y: values[Axis.Y].position_pulses,
            }
            self._scan_round_baseline = dict(baseline)
            self._scan_round_planned = dict(baseline)
            self.diagnostic_event.emit(
                "扫描轮次原始起点已锁定："
                f"X {baseline[Axis.X]} pulse，Y {baseline[Axis.Y]} pulse"
            )
        self.scan_round_prepared.emit(bool(success), str(reason))

    def move_scan_segment(
        self,
        axis,
        pulses,
        direction,
        *,
        predictive=True,
        returning=False,
    ):
        """Start a scan-only absolute move with safe command acceptance."""
        axis, direction = Axis(axis), Direction(direction)
        pulses = int(pulses)
        if (
            not self._connected
            or not self._scan_active
            or self.motion_active
            or self._safety_locked
            or self._scan_round_baseline is None
            or self._scan_round_planned is None
        ):
            self.operation_failed.emit("扫描电机未就绪或已有运动正在执行")
            return None
        if pulses <= 0:
            self.operation_failed.emit("扫描段脉冲数必须大于0")
            return None
        planned_start = int(self._scan_round_planned[axis])
        if returning:
            target = int(self._scan_round_baseline[axis])
        else:
            logical_delta = pulses * direction.sign
            device_delta = -logical_delta if self._reverse(axis) else logical_delta
            target = planned_start + device_delta
        if not 0 <= target <= 0xFFFFFFFF:
            self.operation_failed.emit(
                f"{axis.value} 轴扫描绝对目标 {target} pulse 超出驱动器范围"
            )
            return None
        if target == planned_start:
            self.operation_failed.emit(f"{axis.value} 轴扫描段目标没有变化")
            return None

        operation_id = uuid.uuid4().hex
        started_at = time.monotonic()
        duration = abs(target - planned_start) / self._speeds[axis]
        estimator = MotionHandoffEstimator(
            start_pulses=planned_start,
            target_pulses=target,
            commanded_at=started_at,
            configured_speed_pps=self._speeds[axis],
        )
        self._active_motion = _ActiveMotion(
            operation_id=operation_id,
            kind="scan_move",
            axis=axis,
            start_pulses=planned_start,
            target_pulses=target,
            deadline=started_at + max(5, duration * 3 + 2),
            operation_kind=(
                "scan_return_strict"
                if returning
                else "scan_predictive"
                if predictive
                else "scan_final_strict"
            ),
            started_at=started_at,
            predictive=bool(predictive and not returning),
            acceptance_deadline=started_at + SCAN_ACCEPTANCE_SECONDS,
            estimator=estimator,
            scan_returning=bool(returning),
        )
        # The target is deterministic and remains fixed even when the write
        # acknowledgement is lost. It is safe to retry only after status proves
        # that the axis is still stopped at planned_start.
        self._scan_round_planned[axis] = target
        self.diagnostic_event.emit(
            f"{axis.value} 轴扫描绝对运动：起点 {planned_start} pulse，"
            f"目标 {target} pulse，预测交接 {bool(predictive and not returning)}，"
            f"操作 {operation_id}"
        )
        self.motion_started.emit(operation_id)
        if not self._send_scan_absolute_command():
            self._scan_round_planned[axis] = planned_start
            self._finish_motion(False, "scan_command_not_queued")
            return None
        return operation_id

    def _send_scan_absolute_command(self):
        motion = self._active_motion
        if motion is None or motion.kind != "scan_move":
            return False
        return self.transport.send_request(
            absolute_move_request(
                self.host_settings.address,
                DriverAxis(motion.axis.motor_number),
                motion.target_pulses,
            ),
            tag=("scan_move_ack", motion.operation_id),
            timeout_ms=SCAN_ACK_TIMEOUT_MS,
            read_retries=0,
            action=True,
        )

    def verify_scan_round(self):
        """Strictly verify both planned end targets before acquisition stops."""
        if (
            not self._connected
            or not self._scan_active
            or self.motion_active
            or self._scan_round_planned is None
            or self._scan_verify_values is not None
        ):
            return False
        self._scan_verify_values = {}
        self._scan_verify_deadline = time.monotonic() + SCAN_VERIFY_TIMEOUT_SECONDS
        return self._send_scan_verify_axis(Axis.X)

    def _send_scan_verify_axis(self, axis):
        accepted = self.transport.send_request(
            status_request(
                self.host_settings.address,
                DriverAxis(Axis(axis).motor_number),
            ),
            tag=("scan_verify", Axis(axis)),
            timeout_ms=SCAN_STATUS_TIMEOUT_MS,
            read_retries=0,
        )
        if not accepted:
            self._finish_scan_verification(False, "scan_verify_not_queued")
        return bool(accepted)

    def _continue_scan_verification(self):
        if self._scan_verify_values is not None:
            self._scan_verify_values.clear()
            self._send_scan_verify_axis(Axis.X)

    def _finish_scan_verification(self, success, reason):
        if self._scan_verify_values is None:
            return
        self._scan_aux_timer.stop()
        self._scan_verify_values = None
        self._scan_verify_deadline = 0.0
        self.scan_round_verified.emit(bool(success), str(reason))

    def _start_logical_move(self, axis, logical_pulses):
        axis = Axis(axis)
        if not self._connected or self.motion_active or self._safety_locked:
            self.operation_failed.emit("电机未连接或已有运动正在执行")
            return None
        if not self.mechanics_valid:
            self.operation_failed.emit(
                "驱动板步距角、细分或终点位置与 15 mm / 4800 pulse 换算不一致，"
                "请先在电机设置中恢复建议默认值"
            )
            return None
        if logical_pulses == 0:
            self.operation_failed.emit("移动距离小于一个脉冲")
            return None
        raw = self._axis_device_status[axis]
        start = raw.position_pulses if raw is not None else None
        logical_start = (
            (-start if self._reverse(axis) else start)
            if start is not None
            else None
        )
        device_delta = -logical_pulses if self._reverse(axis) else logical_pulses
        target = start + device_delta if start is not None else None
        logical_target = (
            logical_start + logical_pulses
            if logical_start is not None
            else None
        )
        if self._calibrated[axis] and logical_target is not None and not 0 <= logical_target <= round(MOTOR_TRAVEL_MM * MOTOR_PULSES_PER_MM):
            self.operation_failed.emit(f"{axis.value} 轴目标超出 0～15 mm")
            return None
        operation_id = uuid.uuid4().hex
        duration = abs(logical_pulses) / self._speeds[axis]
        started_at = time.monotonic()
        self._active_motion = _ActiveMotion(
            operation_id,
            "move",
            axis,
            start,
            target,
            started_at + max(5, duration * 3 + 2),
            operation_kind="move",
            started_at=started_at,
        )
        self.diagnostic_event.emit(
            f"{axis.value} 轴相对运动：逻辑 {logical_pulses} pulse，"
            f"驱动器 {device_delta} pulse，操作 {operation_id}"
        )
        self.motion_started.emit(operation_id)
        accepted = self.transport.send_request(
            relative_move_request(self.host_settings.address, DriverAxis(axis.motor_number), device_delta),
            tag=("move_ack", operation_id), timeout_ms=1000, action=True,
        )
        if not accepted:
            self._finish_motion(False, "command_not_queued")
            return None
        return operation_id

    def return_axis_to_zero(self, axis):
        axis = Axis(axis)
        status = self._axis_status(axis)
        if status.software_position_mm is None:
            self.operation_failed.emit(f"{axis.value} 轴软件坐标不可用")
            return None
        pulses = round(-status.software_position_mm * MOTOR_PULSES_PER_MM)
        if not pulses:
            self.operation_failed.emit(f"{axis.value} 轴已在软件零点")
            return None
        return self._start_logical_move(axis, pulses)

    def home(self, axis=None):
        axes = (Axis.X, Axis.Y) if axis is None else (Axis(axis),)
        if (
            not self._connected
            or self.motion_active
            or self._scan_active
            or self._safety_locked
        ):
            self.operation_failed.emit("电机未连接或已有运动正在执行")
            return None
        first = axes[0]
        initial = self._axis_device_status[first]
        if initial is None or not self._axis_limit_known[first]:
            self.operation_failed.emit(
                f"{first.value} 轴限位状态未知，不能开始机械回零"
            )
            return None
        if initial.limit_active:
            self._calibrated[first] = False
            self._refresh_status()
            self.operation_failed.emit(
                f"{first.value} 轴零点限位已闭合，请先执行脱离卡死"
            )
            return None
        operation_id = uuid.uuid4().hex
        first, remaining = axes[0], axes[1:]
        self._calibrated[first] = False
        started_at = time.monotonic()
        self._active_motion = _ActiveMotion(
            operation_id,
            "home",
            first,
            None,
            0,
            started_at + home_timeout_seconds(self._speeds[first]),
            remaining,
            initial_limit_active=False,
            operation_kind="home",
            started_at=started_at,
        )
        self._refresh_status()
        self.diagnostic_event.emit(
            f"机械回零开始：{','.join(item.value for item in axes)}，操作 {operation_id}"
        )
        self.motion_started.emit(operation_id)
        self.transport.send_request(
            home_request(self.host_settings.address, DriverAxis(first.motor_number)), tag=("home_ack", operation_id), action=True
        )
        return operation_id

    def release_stall(self, axis, signed_speed_pps):
        axis = Axis(axis)
        signed_speed = int(signed_speed_pps)
        absolute_speed = abs(signed_speed)
        if not MOTOR_SPEED_MIN_HZ <= absolute_speed <= MOTOR_SPEED_MAX_HZ:
            self.operation_failed.emit(
                "脱离卡死速度绝对值必须在100～14000 pps之间，且不能为0"
            )
            return None
        if (
            not self._connected
            or self.motion_active
            or self._scan_active
            or self._safety_locked
        ):
            self.operation_failed.emit("电机未连接、任务正在运行或停止状态未知")
            return None
        raw = self._axis_device_status[axis]
        if raw is None or not self._axis_limit_known[axis]:
            self.operation_failed.emit(f"{axis.value} 轴限位状态未知")
            return None
        operation_id = uuid.uuid4().hex
        self._calibrated[axis] = False
        started_at = time.monotonic()
        self._active_motion = _ActiveMotion(
            operation_id,
            "stall_release",
            axis,
            raw.position_pulses,
            None,
            started_at + stall_release_timeout_seconds(signed_speed),
            initial_limit_active=raw.limit_active,
            signed_speed_pps=signed_speed,
            operation_kind="stall_release",
            started_at=started_at,
        )
        self._refresh_status()
        self.diagnostic_event.emit(
            f"{axis.value} 轴脱离卡死开始：{signed_speed} pps，操作 {operation_id}"
        )
        self.motion_started.emit(operation_id)
        accepted = self.transport.send_request(
            velocity_mode_request(
                self.host_settings.address,
                DriverAxis(axis.motor_number),
                signed_speed,
            ),
            tag=("release_ack", operation_id),
            timeout_ms=1000,
            action=True,
        )
        if not accepted:
            self._enter_safety_lock(
                f"{axis.value} 轴脱离卡死命令未能排队，停止状态未知"
            )
            self._finish_motion(False, "command_not_queued")
            return None
        return operation_id

    def stop(self):
        active = self._active_motion
        self._active_motion = None
        self._motion_poll_timer.stop()
        self._calibrated = {Axis.X: False, Axis.Y: False}
        self._start_emergency_stop("正在确认急停结果")
        self.diagnostic_event.emit("已向 X/M1、Y/M2 排队发送速度0和位置急停请求")
        if active:
            self._emit_motion_summary(active, False, "user_stop")
            self.motion_finished.emit(active.operation_id, False, "user_stop")
        self._refresh_status()
        return True

    def clear_faults(self):
        if self._safety_locked:
            return self._start_safety_status_check()
        return self.query_status()

    def query_status(self):
        if not self._connected or self.transport.busy:
            return False
        return self.transport.send_request(
            status_request(self.host_settings.address, DriverAxis.X), tag=("status", Axis.X), read_retries=2
        )

    def _poll_idle_status(self):
        if (
            not self._connected
            or self.motion_active
            or self.transport.busy
            or self._safety_status_pending
        ):
            return
        self.transport.send_request(
            status_request(self.host_settings.address, DriverAxis.X),
            tag=("idle_status", Axis.X),
            read_retries=1,
        )

    def _enter_safety_lock(self, reason):
        self._safety_locked = True
        self._safety_lock_reason = str(reason)
        self._calibrated = {Axis.X: False, Axis.Y: False}
        self._refresh_status()

    def _clear_safety_lock(self):
        self._safety_locked = False
        self._safety_lock_reason = ""
        self._refresh_status()

    def _start_emergency_stop(self, reason):
        if not self._connected:
            self._enter_safety_lock(
                "电机停止状态未知，请切断驱动板电源"
            )
            return False
        self._enter_safety_lock(reason)
        requests = []
        for axis in (Axis.X, Axis.Y):
            driver_axis = DriverAxis(axis.motor_number)
            requests.append(
                velocity_mode_request(self.host_settings.address, driver_axis, 0)
            )
            requests.append(stop_request(self.host_settings.address, driver_axis))
        self._emergency_pending = len(requests)
        self.transport.send_emergency(tuple(requests))
        return True

    def _start_safety_status_check(self):
        if not self._connected or self.transport.busy:
            return False
        self._safety_status_pending = True
        return self.transport.send_request(
            status_request(self.host_settings.address, DriverAxis.X),
            tag=("safety_status", Axis.X),
            read_retries=2,
        )

    def _begin_release_stop(self, reason, success):
        motion = self._active_motion
        if motion is None:
            return
        self._active_motion = replace(
            motion,
            kind="stall_release_stopping",
            deadline=time.monotonic() + RELEASE_STOP_CONFIRM_SECONDS,
            stop_reason=str(reason),
            stop_success=bool(success),
            stop_check_count=0,
            fallback_stop_sent=False,
        )
        accepted = self.transport.send_request(
            velocity_mode_request(
                self.host_settings.address,
                DriverAxis(motion.axis.motor_number),
                0,
            ),
            tag=("release_stop", motion.operation_id),
            timeout_ms=500,
            action=True,
        )
        if not accepted:
            self._fallback_release_stop("release_stop_not_queued")

    def _fallback_release_stop(self, reason):
        motion = self._active_motion
        if motion is None:
            return
        if motion.fallback_stop_sent:
            self._retry_release_stop_status(reason)
            return
        self._active_motion = replace(motion, fallback_stop_sent=True)
        self.diagnostic_event.emit(
            f"{motion.axis.value} 轴速度0停止结果未知，补发位置急停：{reason}"
        )
        accepted = self.transport.send_request(
            stop_request(
                self.host_settings.address,
                DriverAxis(motion.axis.motor_number),
            ),
            tag=("release_fallback_stop", motion.operation_id),
            timeout_ms=500,
            action=True,
        )
        if not accepted:
            self._retry_release_stop_status("release_fallback_not_queued")

    def _schedule_release_stop_poll(self):
        self._motion_poll_timer.start(RELEASE_STOP_POLL_MS)

    def _retry_release_stop_status(self, reason):
        motion = self._active_motion
        if motion is None:
            return
        if (
            not self._connected
            or time.monotonic() >= motion.deadline
            or motion.stop_check_count >= RELEASE_STOP_MAX_CHECKS
        ):
            self._lock_active_motion_stop_unknown(reason)
            return
        self.diagnostic_event.emit(
            f"{motion.axis.value} 轴停止状态尚未确认，正在复核：{reason}"
        )
        self._schedule_release_stop_poll()

    def _query_release_stop_status(self):
        motion = self._active_motion
        if motion is None:
            return
        if (
            not self._connected
            or time.monotonic() >= motion.deadline
            or motion.stop_check_count >= RELEASE_STOP_MAX_CHECKS
        ):
            self._lock_active_motion_stop_unknown(
                "release_stop_confirmation_timeout"
            )
            return
        self._active_motion = replace(
            motion,
            stop_check_count=motion.stop_check_count + 1,
        )
        accepted = self.transport.send_request(
            status_request(
                self.host_settings.address,
                DriverAxis(motion.axis.motor_number),
            ),
            tag=("release_stop_status", motion.operation_id),
            read_retries=2,
        )
        if not accepted and self._active_motion is not None:
            self._retry_release_stop_status(
                "release_stop_status_not_queued"
            )

    def _begin_position_stop(self, reason):
        motion = self._active_motion
        if motion is None:
            return
        self._active_motion = replace(
            motion,
            kind="position_stopping",
            stop_reason=str(reason),
            stop_success=False,
        )
        accepted = self.transport.send_request(
            stop_request(
                self.host_settings.address,
                DriverAxis(motion.axis.motor_number),
            ),
            tag=("motion_stop", motion.operation_id),
            timeout_ms=500,
            action=True,
        )
        if not accepted:
            self._lock_active_motion_stop_unknown("motion_stop_not_queued")

    def _query_position_stop_status(self):
        motion = self._active_motion
        if motion is None:
            return
        self.transport.send_request(
            status_request(
                self.host_settings.address,
                DriverAxis(motion.axis.motor_number),
            ),
            tag=("motion_stop_status", motion.operation_id),
            read_retries=2,
        )

    def _lock_active_motion_stop_unknown(self, reason):
        motion = self._active_motion
        axis_name = motion.axis.value if motion else "电机"
        self._enter_safety_lock(
            f"{axis_name} 轴停止状态未知，请切断驱动板电源（{reason}）"
        )
        self._finish_motion(False, "stop_unconfirmed")

    def _schedule_motion_poll(self, delay_ms=None):
        self._motion_poll_timer.start(
            MOTION_STATUS_REPOLL_MS if delay_ms is None else max(1, int(delay_ms))
        )

    def _send_motion_status_query(self):
        motion = self._active_motion
        if motion is None or not self._connected:
            return False
        self._active_motion = replace(
            motion,
            status_query_count=motion.status_query_count + 1,
        )
        scan_motion = motion.kind == "scan_move"
        return self.transport.send_request(
            status_request(
                self.host_settings.address,
                DriverAxis(motion.axis.motor_number),
            ),
            tag=(
                "scan_motion_status" if scan_motion else "motion_status",
                motion.operation_id,
            ),
            timeout_ms=(
                SCAN_STATUS_TIMEOUT_MS if scan_motion else MOTION_STATUS_TIMEOUT_MS
            ),
            read_retries=(0 if scan_motion else MOTION_STATUS_READ_RETRIES),
        )

    def _handle_motion_deadline(self, motion):
        if motion.kind == "stall_release":
            if motion.initial_limit_active:
                self._begin_release_stop(
                    "stall_release_limit_not_released", False
                )
            else:
                self._begin_release_stop("stall_release_timeout", True)
        elif motion.kind in ("home", "move", "scan_move"):
            self._begin_position_stop("motion_timeout")

    def _poll_motion(self):
        motion = self._active_motion
        if motion is None or not self._connected:
            return
        if motion.kind == "stall_release_stopping":
            if (
                time.monotonic() >= motion.deadline
                or motion.stop_check_count >= RELEASE_STOP_MAX_CHECKS
            ):
                self._lock_active_motion_stop_unknown(
                    "release_stop_confirmation_timeout"
                )
            elif self.transport.busy:
                self._schedule_release_stop_poll()
            else:
                self._query_release_stop_status()
            return
        now = time.monotonic()
        if motion.kind == "scan_move" and not motion.command_accepted:
            if now >= motion.acceptance_deadline:
                self._finish_motion(False, "scan_command_acceptance_timeout")
            elif self.transport.busy:
                self._schedule_motion_poll(SCAN_STATUS_REPOLL_MS)
            else:
                self._send_scan_acceptance_query()
            return
        if motion.kind == "scan_move" and motion.handoff_deadline:
            if now >= motion.handoff_deadline:
                self._finish_motion(True, "handoff_predicted")
            else:
                self._schedule_motion_poll(
                    round((motion.handoff_deadline - now) * 1000)
                )
            return
        if now > motion.deadline:
            self._handle_motion_deadline(motion)
            return
        if self.transport.busy:
            self._schedule_motion_poll()
            return
        self._send_motion_status_query()

    def _send_scan_acceptance_query(self):
        motion = self._active_motion
        if motion is None or motion.kind != "scan_move":
            return False
        self._active_motion = replace(
            motion,
            status_query_count=motion.status_query_count + 1,
        )
        return self.transport.send_request(
            status_request(
                self.host_settings.address,
                DriverAxis(motion.axis.motor_number),
            ),
            tag=("scan_accept_status", motion.operation_id),
            timeout_ms=SCAN_STATUS_TIMEOUT_MS,
            read_retries=0,
        )

    def _handle_scan_acceptance_status(self, response):
        motion = self._active_motion
        if motion is None or motion.kind != "scan_move":
            return
        raw = decode_axis_status(response.registers)
        self._axis_device_status[motion.axis] = raw
        self._axis_limit_known[motion.axis] = True
        self._refresh_status()
        now = time.monotonic()
        start = int(motion.start_pulses)
        target = int(motion.target_pulses)
        direction = 1 if target > start else -1
        progress = (raw.position_pulses - start) * direction
        distance = abs(target - start)
        if raw.limit_active and target != 0:
            self._finish_motion(False, "unexpected_zero_limit")
            return
        if progress > distance:
            self._finish_motion(False, "scan_target_overshoot")
            return
        if (
            raw.moving
            and progress < 0
            and motion.estimator is not None
            and abs(progress)
            > motion.estimator.config.near_target_pulses
        ):
            self._finish_motion(False, "scan_reverse_motion")
            return
        if abs(raw.position_pulses - target) <= 1 and not raw.moving:
            self._active_motion = replace(motion, command_accepted=True)
            self._finish_motion(True, "completed_strict")
            return
        if raw.moving and progress > 0:
            motion = replace(motion, command_accepted=True)
            self._active_motion = motion
            self._handle_scan_motion_status(motion, raw)
            return
        if not raw.moving and raw.position_pulses == start:
            if now >= motion.acceptance_deadline:
                self._finish_motion(False, "scan_command_acceptance_timeout")
                return
            self._active_motion = replace(
                motion,
                acceptance_retry_count=motion.acceptance_retry_count + 1,
            )
            self.diagnostic_event.emit(
                f"{motion.axis.value} 轴扫描写入未执行，重发相同绝对目标 "
                f"{target} pulse，操作 {motion.operation_id}"
            )
            if not self._send_scan_absolute_command():
                self._finish_motion(False, "scan_command_retry_not_queued")
            return
        if raw.moving and progress <= 0 and now < motion.acceptance_deadline:
            self._schedule_motion_poll(SCAN_STATUS_REPOLL_MS)
            return
        self._finish_motion(False, "scan_command_acceptance_ambiguous")

    def _handle_scan_prepare_status(self, axis, response):
        if self._scan_prepare_values is None:
            return
        axis = Axis(axis)
        raw = decode_axis_status(response.registers)
        self._axis_device_status[axis] = raw
        self._axis_limit_known[axis] = True
        self._refresh_status()
        if raw.moving:
            self._finish_scan_preparation(
                False, f"{axis.value}_axis_still_moving_before_scan"
            )
            return
        self._scan_prepare_values[axis] = raw
        if axis is Axis.X:
            accepted = self.transport.send_request(
                status_request(self.host_settings.address, DriverAxis.Y),
                tag=("scan_prepare", Axis.Y),
                timeout_ms=SCAN_STATUS_TIMEOUT_MS,
                read_retries=0,
            )
            if not accepted:
                self._finish_scan_preparation(False, "scan_prepare_not_queued")
        else:
            self._finish_scan_preparation(True, "prepared")

    def _handle_scan_verify_status(self, axis, response):
        if self._scan_verify_values is None or self._scan_round_planned is None:
            return
        axis = Axis(axis)
        raw = decode_axis_status(response.registers)
        self._axis_device_status[axis] = raw
        self._axis_limit_known[axis] = True
        self._refresh_status()
        now = time.monotonic()
        target = int(self._scan_round_planned[axis])
        if raw.moving:
            if now >= self._scan_verify_deadline:
                self._finish_scan_verification(
                    False, f"{axis.value}_axis_scan_verify_timeout"
                )
            else:
                self._scan_aux_timer.start(SCAN_STATUS_REPOLL_MS)
            return
        if abs(raw.position_pulses - target) > 1:
            self._finish_scan_verification(
                False,
                f"{axis.value}_axis_scan_end_mismatch_{raw.position_pulses}_{target}",
            )
            return
        if raw.limit_active and target != 0:
            self._finish_scan_verification(
                False, f"{axis.value}_axis_unexpected_zero_limit"
            )
            return
        self._scan_verify_values[axis] = raw
        if axis is Axis.X:
            self._send_scan_verify_axis(Axis.Y)
        else:
            self.diagnostic_event.emit(
                "扫描终点严格校验通过："
                f"X {self._scan_round_planned[Axis.X]} pulse，"
                f"Y {self._scan_round_planned[Axis.Y]} pulse"
            )
            self._finish_scan_verification(True, "verified")

    def _record_scan_sample(self, motion, raw, captured_at):
        estimator = motion.estimator
        if estimator is None:
            return motion
        last = estimator.last_sample
        if last is not None:
            direction = estimator.direction
            if (raw.position_pulses - last.position_pulses) * direction <= 0:
                return motion
        try:
            estimator.add_sample(raw.position_pulses, captured_at)
        except ValueError:
            return motion
        return replace(motion, estimator=estimator)

    def _handle_scan_motion_status(self, motion, raw):
        now = time.monotonic()
        start = int(motion.start_pulses)
        target = int(motion.target_pulses)
        direction = 1 if target > start else -1
        distance = abs(target - start)
        progress = (raw.position_pulses - start) * direction
        if raw.limit_active and target != 0:
            self._finish_motion(False, "unexpected_zero_limit")
            return
        if progress > distance:
            self._finish_motion(False, "scan_target_overshoot")
            return
        if raw.moving:
            # Same-direction substeps can be handed off while the previous
            # absolute target is still being approached. Such a pre-start
            # sample is not trusted for prediction, but it is not reversal.
            estimator = motion.estimator
            if (
                progress < 0
                and estimator is not None
                and abs(progress) > estimator.config.near_target_pulses
            ):
                self._finish_motion(False, "scan_reverse_motion")
                return
            if estimator is not None and estimator.last_sample is not None:
                incremental = (
                    raw.position_pulses
                    - estimator.last_sample.position_pulses
                ) * direction
                if incremental < 0:
                    self._finish_motion(False, "scan_reverse_motion")
                    return
            if progress > 0:
                motion = self._record_scan_sample(motion, raw, now)
                self._active_motion = motion
            estimator = motion.estimator
            if (
                motion.predictive
                and estimator is not None
                and estimator.is_near_target
            ):
                handoff = estimator.handoff_deadline(now)
                self._active_motion = replace(
                    motion, handoff_deadline=float(handoff)
                )
                self.diagnostic_event.emit(
                    f"{motion.axis.value} 轴扫描进入预测交接区："
                    f"剩余 {estimator.remaining_pulses} pulse，操作 {motion.operation_id}"
                )
                self._schedule_motion_poll(round((handoff - now) * 1000))
            else:
                self._schedule_motion_poll(SCAN_STATUS_REPOLL_MS)
            return
        if abs(raw.position_pulses - target) <= 1:
            self._finish_motion(
                True,
                "completed_strict" if not motion.predictive else "completed_observed",
            )
            return
        self._finish_motion(False, "scan_position_mismatch")

    def _handle_motion_status(self, response):
        motion = self._active_motion
        if motion is None:
            return
        raw = decode_axis_status(response.registers)
        self._axis_device_status[motion.axis] = raw
        self._axis_limit_known[motion.axis] = True
        if (
            motion.kind == "home"
            and motion.initial_limit_active is False
            and raw.limit_active
            and not motion.limit_transition_seen
        ):
            motion = replace(motion, limit_transition_seen=True)
            self._active_motion = motion
        self._refresh_status()
        if motion.kind == "scan_move":
            self._handle_scan_motion_status(motion, raw)
            return
        if motion.kind == "stall_release":
            if motion.initial_limit_active and not raw.limit_active:
                self._begin_release_stop(
                    "stall_release_limit_released", True
                )
                return
            if motion.initial_limit_active is False and raw.limit_active:
                self._begin_release_stop(
                    "stall_release_wrong_direction", False
                )
                return
            if not raw.moving:
                self._begin_release_stop(
                    "stall_release_stopped_unexpectedly", False
                )
                return
            self._schedule_motion_poll()
            return
        if raw.moving:
            self._schedule_motion_poll()
            return
        if motion.kind == "home":
            if (
                not motion.limit_transition_seen
                or raw.position_pulses != 0
                or not raw.limit_active
            ):
                self._finish_motion(False, "home_zero_limit_not_confirmed")
                return
            self._calibrated[motion.axis] = True
            self._software_zero[motion.axis] = 0
            if motion.remaining_home_axes:
                next_axis = motion.remaining_home_axes[0]
                next_raw = self._axis_device_status[next_axis]
                if (
                    next_raw is None
                    or not self._axis_limit_known[next_axis]
                    or next_raw.limit_active
                ):
                    self._finish_motion(
                        False,
                        f"{next_axis.value}_home_requires_open_limit",
                    )
                    return
                self._calibrated[next_axis] = False
                self._active_motion = replace(
                    motion,
                    axis=next_axis,
                    start_pulses=None,
                    target_pulses=0,
                    deadline=time.monotonic()
                    + home_timeout_seconds(self._speeds[next_axis]),
                    remaining_home_axes=motion.remaining_home_axes[1:],
                    initial_limit_active=False,
                    limit_transition_seen=False,
                )
                self.transport.send_request(
                    home_request(self.host_settings.address, DriverAxis(next_axis.motor_number)),
                    tag=("home_ack", motion.operation_id), action=True,
                )
                return
        else:
            expected_zero_limit = self._calibrated[motion.axis] and motion.target_pulses == 0 and raw.limit_active
            if raw.limit_active and not expected_zero_limit:
                self._finish_motion(False, "unexpected_zero_limit")
                return
            if motion.target_pulses is not None and abs(raw.position_pulses - motion.target_pulses) > 1:
                self._finish_motion(False, "position_mismatch")
                return
        self._refresh_status()
        self._finish_motion(True, "completed")

    def _finish_motion(self, success, reason):
        motion = self._active_motion
        self._active_motion = None
        self._motion_poll_timer.stop()
        if motion:
            self._emit_motion_summary(motion, success, reason)
            self.motion_finished.emit(motion.operation_id, bool(success), str(reason))
        if not success:
            self.operation_failed.emit(str(reason))

    def _emit_motion_summary(self, motion, success, reason):
        started_at = motion.started_at or time.monotonic()
        elapsed_ms = max(
            0, round((time.monotonic() - started_at) * 1000)
        )
        operation_kind = motion.operation_kind or motion.kind
        result = "成功" if success else "失败"
        self.diagnostic_event.emit(
            f"{motion.axis.value} 轴动作汇总：类型 {operation_kind}，"
            f"耗时 {elapsed_ms} ms，状态查询 {motion.status_query_count} 次，"
            f"读取失败 {motion.status_failure_count} 次，结果 {result}，"
            f"原因 {reason}，操作 {motion.operation_id}"
        )

    @Slot(object, object)
    def _on_command_completed(self, tag, response):
        kind = tag[0] if isinstance(tag, tuple) and tag else ""
        if kind == "probe":
            try:
                decode_supported_identity(response.registers)
            except (TypeError, ValueError):
                self._try_next_candidate()
                return
            self._probing = False
            self._pending_configuration.clear()
            self._start_initialization()
        elif kind == "init":
            try:
                self._continue_initialization(tag[1], response)
            except (TypeError, ValueError) as exc:
                self.operation_failed.emit(f"驱动板配置解析失败：{exc}")
                self.disconnect()
        elif kind in ("move_ack", "home_ack", "release_ack"):
            self._schedule_motion_poll()
        elif kind == "scan_move_ack":
            motion = self._active_motion
            if (
                motion is not None
                and len(tag) > 1
                and tag[1] == motion.operation_id
            ):
                self._active_motion = replace(
                    motion, command_accepted=True
                )
                # Query promptly after the write ACK so short 1 mm segments
                # still have a chance to produce a trusted moving sample.
                self._schedule_motion_poll(1)
        elif kind == "motion_status":
            self._handle_motion_status(response)
        elif kind == "scan_motion_status":
            self._handle_motion_status(response)
        elif kind == "scan_accept_status":
            self._handle_scan_acceptance_status(response)
        elif kind == "scan_prepare":
            self._handle_scan_prepare_status(tag[1], response)
        elif kind == "scan_verify":
            self._handle_scan_verify_status(tag[1], response)
        elif kind == "release_stop":
            self._query_release_stop_status()
        elif kind == "release_fallback_stop":
            self._schedule_release_stop_poll()
        elif kind == "release_stop_status":
            motion = self._active_motion
            if motion is None:
                return
            raw = decode_axis_status(response.registers)
            self._axis_device_status[motion.axis] = raw
            self._axis_limit_known[motion.axis] = True
            self._refresh_status()
            if raw.moving:
                if motion.fallback_stop_sent:
                    self._retry_release_stop_status(
                        "release_still_running_after_fallback_stop"
                    )
                else:
                    self._fallback_release_stop(
                        "release_still_running_after_velocity_stop"
                    )
                return
            self._finish_motion(
                motion.stop_success,
                motion.stop_reason or "stall_release_completed",
            )
        elif kind == "motion_stop":
            self._query_position_stop_status()
        elif kind == "motion_stop_status":
            motion = self._active_motion
            if motion is None:
                return
            raw = decode_axis_status(response.registers)
            self._axis_device_status[motion.axis] = raw
            self._axis_limit_known[motion.axis] = True
            self._refresh_status()
            if raw.moving:
                self._lock_active_motion_stop_unknown(
                    "motion_still_running_after_stop"
                )
                return
            self._finish_motion(False, motion.stop_reason or "motion_stopped")
        elif kind == "stop":
            self._emergency_pending = max(0, self._emergency_pending - 1)
            if self._emergency_pending == 0:
                self._start_safety_status_check()
        elif kind in ("safety_status", "idle_status"):
            axis = tag[1]
            self._axis_device_status[axis] = decode_axis_status(response.registers)
            self._axis_limit_known[axis] = True
            self._refresh_status()
            if axis is Axis.X:
                self.transport.send_request(
                    status_request(self.host_settings.address, DriverAxis.Y),
                    tag=(kind, Axis.Y),
                    read_retries=2 if kind == "safety_status" else 1,
                )
            elif kind == "safety_status":
                self._safety_status_pending = False
                if any(
                    raw is None or raw.moving
                    for raw in self._axis_device_status.values()
                ):
                    self._enter_safety_lock(
                        "电机停止状态未知，请切断驱动板电源"
                    )
                else:
                    self._clear_safety_lock()
        elif kind == "speed":
            axis, signed_speed, absolute_speed = tag[1:4]
            self._speeds[axis] = absolute_speed
            if self._configuration:
                axis_config = self._configuration.x if axis is Axis.X else self._configuration.y
                updated = replace(axis_config, position_speed_pps=absolute_speed)
                self._configuration = replace(self._configuration, **({"x": updated} if axis is Axis.X else {"y": updated}))
                self.configuration_changed.emit(self._configuration)
            message = (
                f"运动速度已设为 {absolute_speed} pps；"
                f"脱离卡死速度为 {signed_speed} pps"
            )
            self.speed_applied.emit(axis, signed_speed, absolute_speed, message)
        elif kind == "config_write":
            if tag[1] == "communication.address":
                desired = self._desired_host_settings
                self._reconnect_config_port(
                    desired.address, self._pre_apply_host_settings.baud_rate
                )
            elif tag[1] == "communication.baud_rate":
                desired = self._desired_host_settings
                self._reconnect_config_port(
                    desired.address, desired.baud_rate
                )
            else:
                self._send_next_config_op()
        elif kind == "config_reconnect":
            try:
                decode_supported_identity(response.registers)
            except (TypeError, ValueError) as exc:
                self._fail_configuration_apply(
                    f"新通信参数下设备身份校验失败：{exc}"
                )
                return
            self._config_reconnect_pending = False
            self._connected = True
            self._send_next_config_op()
        elif kind == "status":
            axis = tag[1]
            self._axis_device_status[axis] = decode_axis_status(response.registers)
            self._axis_limit_known[axis] = True
            if axis is Axis.X:
                self.transport.send_request(
                    status_request(self.host_settings.address, DriverAxis.Y), tag=("status", Axis.Y), read_retries=2
                )
            else:
                self._refresh_status()

    @Slot(object, str)
    def _on_command_failed(self, tag, reason):
        kind = tag[0] if isinstance(tag, tuple) and tag else ""
        if kind == "probe" and self._probing:
            self._try_next_candidate()
        elif kind in ("motion_status", "scan_motion_status"):
            motion = self._active_motion
            if (
                motion is None
                or len(tag) < 2
                or tag[1] != motion.operation_id
            ):
                return
            motion = replace(
                motion,
                status_failure_count=motion.status_failure_count + 1,
            )
            self._active_motion = motion
            if (
                not self._connected
                or reason in ("not_connected", "disconnected")
            ):
                self._finish_motion(False, reason)
            elif (
                kind == "scan_motion_status"
                and motion.predictive
                and motion.estimator is not None
                and (
                    motion.estimator.timeout_is_end_phase
                    or motion.estimator.prediction_due(time.monotonic())
                )
            ):
                deadline = motion.estimator.handoff_deadline(
                    time.monotonic()
                )
                self._active_motion = replace(
                    motion, handoff_deadline=float(deadline)
                )
                self._schedule_motion_poll(
                    round(max(0.0, deadline - time.monotonic()) * 1000)
                )
            elif time.monotonic() >= motion.deadline:
                self._handle_motion_deadline(motion)
            else:
                self._schedule_motion_poll(
                    SCAN_STATUS_REPOLL_MS
                    if kind == "scan_motion_status"
                    else None
                )
        elif kind == "scan_accept_status":
            motion = self._active_motion
            if (
                motion is None
                or len(tag) < 2
                or tag[1] != motion.operation_id
            ):
                return
            self._active_motion = replace(
                motion,
                status_failure_count=motion.status_failure_count + 1,
            )
            if time.monotonic() >= motion.acceptance_deadline:
                self._finish_motion(
                    False, "scan_command_acceptance_timeout"
                )
            else:
                # A missing read cannot prove whether the write executed, so
                # do not resend it; read status again within the 1 s window.
                self._schedule_motion_poll(SCAN_STATUS_REPOLL_MS)
        elif kind == "scan_move_ack":
            if reason == "result_unknown" and self._active_motion is not None:
                self.diagnostic_event.emit(
                    "扫描绝对目标写入应答超时，正在只读确认是否已执行"
                )
                self._send_scan_acceptance_query()
            else:
                self._finish_motion(False, reason)
        elif kind == "scan_prepare":
            self._finish_scan_preparation(False, f"scan_prepare_{reason}")
        elif kind == "scan_verify":
            if (
                self._scan_verify_values is not None
                and time.monotonic() < self._scan_verify_deadline
            ):
                self._scan_aux_timer.start(SCAN_STATUS_REPOLL_MS)
            else:
                self._finish_scan_verification(
                    False, f"scan_verify_{reason}"
                )
        elif kind in ("move_ack", "home_ack"):
            if (
                kind in ("move_ack", "home_ack")
                and reason == "result_unknown"
                and self._active_motion is not None
            ):
                motion = self._active_motion
                self.diagnostic_event.emit(
                    f"{motion.axis.value} 轴动作写入应答超时，正在只读核对位置和状态"
                )
                self._send_motion_status_query()
            else:
                self._finish_motion(False, reason)
        elif kind == "release_ack":
            self.diagnostic_event.emit(
                "脱离卡死启动结果未知，正在发送速度0停止"
            )
            self._begin_release_stop("release_start_result_unknown", False)
        elif kind == "release_stop":
            self._fallback_release_stop(reason)
        elif kind == "release_fallback_stop":
            self._retry_release_stop_status(reason)
        elif kind == "release_stop_status":
            motion = self._active_motion
            if motion is None:
                return
            if motion.fallback_stop_sent:
                self._retry_release_stop_status(reason)
            else:
                self._fallback_release_stop(reason)
        elif kind in ("motion_stop", "motion_stop_status"):
            self._lock_active_motion_stop_unknown(reason)
        elif kind == "stop":
            self._emergency_pending = max(0, self._emergency_pending - 1)
            self._safety_lock_reason = (
                "急停写入结果未知，正在读取两轴状态"
            )
            self._refresh_status()
            if self._emergency_pending == 0:
                self._start_safety_status_check()
        elif kind == "safety_status":
            self._safety_status_pending = False
            self._enter_safety_lock(
                "电机停止状态未知，请切断驱动板电源"
            )
        elif kind == "idle_status":
            axis = tag[1]
            self._axis_limit_known[axis] = False
            self._refresh_status()
            if axis is Axis.X and self._connected and not self.transport.busy:
                self.transport.send_request(
                    status_request(self.host_settings.address, DriverAxis.Y),
                    tag=("idle_status", Axis.Y),
                    read_retries=1,
                )
        elif kind == "init":
            self.operation_failed.emit(f"读取驱动板配置失败：{reason}")
            self.disconnect()
            if self._desired_configuration is not None:
                if self._pre_apply_host_settings is not None:
                    self.host_settings = self._pre_apply_host_settings
                    self.settings_store.save(self.host_settings)
                self._desired_configuration = None
                self._desired_host_settings = None
                self._pre_apply_host_settings = None
                self._config_ops.clear()
                self.configuration_apply_finished.emit(False, f"配置回读失败：{reason}")
        elif kind == "config_write":
            self._config_ops.clear()
            self._configuration_result_unknown = str(tag[1])
            if str(tag[1]).startswith("communication."):
                desired = self._desired_host_settings
                previous = self._pre_apply_host_settings
                port = self._confirmed_candidate.port_name
                candidate = MotorPortCandidate(port, port, preferred=True)
                self._connected = False
                self.transport.disconnect_port()
                self._probe_candidates = (candidate, candidate)
                self._probe_profiles = (
                    (desired.address, desired.baud_rate),
                    (previous.address, previous.baud_rate),
                )
                self._probe_original_host = previous
                self._probe_index = -1
                self._probing = True
                self._try_next_candidate()
            else:
                self._pending_configuration.clear()
                self._start_initialization()
        elif kind == "config_reconnect":
            self._fail_configuration_apply(
                f"新通信参数下设备身份读取失败：{reason}"
            )
        elif kind == "speed" and reason == "result_unknown":
            self.operation_failed.emit(
                f"{tag[1].value} 轴速度写入结果未知，正在重新读取驱动板配置"
            )
            self._pending_configuration.clear()
            self._start_initialization()
        else:
            self.operation_failed.emit(str(reason))

    def shutdown(self):
        if self.motion_active:
            self.stop()
        self.transport.shutdown()
