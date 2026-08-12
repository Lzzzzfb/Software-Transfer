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
    communication_request,
    configuration_changes,
    home_request,
    identity_request,
    position_speed_request,
    relative_move_request,
    status_request,
    stop_request,
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
)
from .modbus_rtu import build_write_single
from .settings_store import HostMotorSettings, MotorSettingsStore
from .transport import MotorSerialTransport


@dataclass(frozen=True)
class _ActiveMotion:
    operation_id: str
    kind: str
    axis: Axis
    start_pulses: int | None
    target_pulses: int | None
    deadline: float
    remaining_home_axes: tuple[Axis, ...] = ()


def _unknown_axis(axis):
    return MotorAxisStatus(Axis(axis), None, None, False, False, False)


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
        self._calibrated = {Axis.X: False, Axis.Y: False}
        self._software_zero = {Axis.X: 0, Axis.Y: 0}
        self._speeds = {Axis.X: MOTOR_SPEED_DEFAULT_HZ, Axis.Y: MOTOR_SPEED_DEFAULT_HZ}
        self._active_motion: _ActiveMotion | None = None
        self._configuration: DeviceConfiguration | None = None
        self._pending_configuration = {}
        self._initialization_step = ""
        self._scan_active = False
        self._config_ops = []
        self._desired_configuration = None
        self._desired_host_settings = None
        self._pre_apply_host_settings = None
        self._configuration_result_unknown = ""
        self._config_reconnect_pending = False

        self._motion_poll_timer = QtCore.QTimer(self)
        self._motion_poll_timer.setSingleShot(True)
        self._motion_poll_timer.timeout.connect(self._poll_motion)
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
        self._scan_active = bool(active)

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
            self.connection_changed.emit(False, "未识别到 LK-MD2202")
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
            self._connected = False
            self._active_motion = None
            self._calibrated = {Axis.X: False, Axis.Y: False}
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
            next_step, request = "y_status", status_request(self.host_settings.address, DriverAxis.Y)
        else:
            self._axis_device_status[Axis.Y] = decode_axis_status(response.registers)
            self._initialization_step = ""
            self._connected = True
            self._refresh_status()
            candidate = self._probe_candidates[self._probe_index]
            self._confirmed_candidate = replace(candidate, confirmed=True)
            self.host_settings = replace(self.host_settings, port_name=candidate.system_location or candidate.port_name)
            if self._desired_configuration is None:
                self.settings_store.save(self.host_settings)
            self.connection_changed.emit(True, candidate.port_name)
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
        self._confirmed_candidate = None
        self._calibrated = {Axis.X: False, Axis.Y: False}
        self._axis_device_status = {Axis.X: None, Axis.Y: None}
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
                axis, raw.position_pulses, software_mm, self._calibrated[axis], raw.moving, raw.limit_active,
                "LIMIT" if raw.limit_active and not raw.moving else "DONE" if not raw.moving else "MOVING",
                logical_pulses / MOTOR_PULSES_PER_MM if self._calibrated[axis] else None,
            ))
        self._status = MotorStatus(*statuses)
        self.status_changed.emit(self._status)

    def clear_software_zero(self, axis):
        axis = Axis(axis)
        if self.motion_active or self._scan_active:
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
        speed_hz = int(speed_hz)
        if not MOTOR_SPEED_MIN_HZ <= speed_hz <= MOTOR_SPEED_MAX_HZ:
            self.operation_failed.emit("速度必须在100～14000 pps之间")
            return False
        if not self._connected or self.motion_active or self._scan_active:
            self.operation_failed.emit("电机未连接或任务正在运行")
            return False
        return self.transport.send_request(
            position_speed_request(self.host_settings.address, DriverAxis(axis.motor_number), speed_hz),
            tag=("speed", axis, speed_hz), action=True,
        )

    def apply_configuration(self, desired, host_settings):
        if not isinstance(desired, DeviceConfiguration) or not isinstance(host_settings, HostMotorSettings):
            raise TypeError("invalid motor configuration")
        if not self._connected or self.motion_active or self._scan_active or self._configuration is None:
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

    def _start_logical_move(self, axis, logical_pulses):
        axis = Axis(axis)
        if not self._connected or self.motion_active:
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
        self._active_motion = _ActiveMotion(operation_id, "move", axis, start, target, time.monotonic() + max(5, duration * 3 + 2))
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
        if not self._connected or self.motion_active or self._scan_active:
            self.operation_failed.emit("电机未连接或已有运动正在执行")
            return None
        operation_id = uuid.uuid4().hex
        first, remaining = axes[0], axes[1:]
        self._active_motion = _ActiveMotion(operation_id, "home", first, None, 0, time.monotonic() + 60, remaining)
        self.diagnostic_event.emit(
            f"机械回零开始：{','.join(item.value for item in axes)}，操作 {operation_id}"
        )
        self.motion_started.emit(operation_id)
        self.transport.send_request(
            home_request(self.host_settings.address, DriverAxis(first.motor_number)), tag=("home_ack", operation_id), action=True
        )
        return operation_id

    def stop(self):
        active = self._active_motion
        self._active_motion = None
        self._motion_poll_timer.stop()
        requests = tuple(stop_request(self.host_settings.address, DriverAxis(axis.motor_number)) for axis in (Axis.X, Axis.Y))
        self.transport.send_emergency(requests)
        self.diagnostic_event.emit("已向 X/M1、Y/M2 排队发送停止请求")
        if active:
            self.motion_finished.emit(active.operation_id, False, "user_stop")

    def clear_faults(self):
        return self.query_status()

    def query_status(self):
        if not self._connected or self.transport.busy:
            return False
        return self.transport.send_request(
            status_request(self.host_settings.address, DriverAxis.X), tag=("status", Axis.X), read_retries=2
        )

    def _schedule_motion_poll(self):
        self._motion_poll_timer.start(50)

    def _poll_motion(self):
        motion = self._active_motion
        if motion is None or not self._connected:
            return
        if time.monotonic() > motion.deadline:
            self._finish_motion(False, "motion_timeout")
            self.stop()
            return
        if self.transport.busy:
            self._schedule_motion_poll()
            return
        self.transport.send_request(
            status_request(self.host_settings.address, DriverAxis(motion.axis.motor_number)),
            tag=("motion_status", motion.operation_id), read_retries=2,
        )

    def _handle_motion_status(self, response):
        motion = self._active_motion
        if motion is None:
            return
        raw = decode_axis_status(response.registers)
        self._axis_device_status[motion.axis] = raw
        self._refresh_status()
        if raw.moving:
            self._schedule_motion_poll()
            return
        if motion.kind == "home":
            if raw.position_pulses != 0 or not raw.limit_active:
                self._finish_motion(False, "home_zero_limit_not_confirmed")
                return
            self._calibrated[motion.axis] = True
            self._software_zero[motion.axis] = 0
            if motion.remaining_home_axes:
                next_axis = motion.remaining_home_axes[0]
                self._active_motion = replace(
                    motion, axis=next_axis, start_pulses=None, target_pulses=0,
                    deadline=time.monotonic() + 60, remaining_home_axes=motion.remaining_home_axes[1:]
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
            self.motion_finished.emit(motion.operation_id, bool(success), str(reason))
        if not success:
            self.operation_failed.emit(str(reason))

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
        elif kind in ("move_ack", "home_ack"):
            self._schedule_motion_poll()
        elif kind == "motion_status":
            self._handle_motion_status(response)
        elif kind == "speed":
            self._speeds[tag[1]] = tag[2]
            if self._configuration:
                axis_config = self._configuration.x if tag[1] is Axis.X else self._configuration.y
                updated = replace(axis_config, position_speed_pps=tag[2])
                self._configuration = replace(self._configuration, **({"x": updated} if tag[1] is Axis.X else {"y": updated}))
                self.configuration_changed.emit(self._configuration)
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
        elif kind in ("move_ack", "home_ack", "motion_status"):
            if (
                kind in ("move_ack", "home_ack")
                and reason == "result_unknown"
                and self._active_motion is not None
            ):
                motion = self._active_motion
                self.diagnostic_event.emit(
                    f"{motion.axis.value} 轴动作写入应答超时，正在只读核对位置和状态"
                )
                self.transport.send_request(
                    status_request(
                        self.host_settings.address,
                        DriverAxis(motion.axis.motor_number),
                    ),
                    tag=("motion_status", motion.operation_id),
                    read_retries=2,
                )
            else:
                self._finish_motion(False, reason)
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
