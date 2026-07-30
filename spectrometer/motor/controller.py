"""High-level motor discovery, coordinate, and motion controller."""

from __future__ import annotations

from dataclasses import dataclass, replace
import time
import uuid

from ..qt import QtCore, Signal, Slot
from .discovery import (
    MotorPortCandidate,
    available_port_records,
    find_motor_candidates,
    is_motor_identity,
)
from .models import (
    MOTOR_SPEED_DEFAULT_HZ,
    MOTOR_TRAVEL_MM,
    Axis,
    Direction,
    MotorAxisStatus,
    MotorStatus,
    Position,
)
from .protocol import (
    MotorResponse,
    build_clear_fault_command,
    build_home_command,
    build_id_command,
    build_move_command,
    build_position_set_command,
    build_speed_command,
    build_status_command,
    build_stop_command,
)
from .state_store import MotorStateStore
from .transport import MotorSerialTransport


@dataclass(frozen=True)
class _ActiveMotion:
    operation_id: str
    kind: str
    axis: Axis | None
    target_mm: float | None
    deadline: float


def _unknown_axis(axis: Axis) -> MotorAxisStatus:
    return MotorAxisStatus(
        axis=axis,
        position_mm=None,
        position_valid=False,
        moving=False,
        zero_limit_active=False,
    )


def _unknown_status() -> MotorStatus:
    return MotorStatus(
        _unknown_axis(Axis.X),
        _unknown_axis(Axis.Y),
        _unknown_axis(Axis.Z),
    )


class MotorController(QtCore.QObject):
    candidates_changed = Signal(object)
    connection_changed = Signal(bool, str)
    status_changed = Signal(object)
    operation_failed = Signal(str)
    diagnostic_event = Signal(str)
    motion_started = Signal(str)
    motion_finished = Signal(str, bool, str)

    def __init__(
        self,
        parent=None,
        *,
        transport=None,
        state_store: MotorStateStore,
        port_provider=available_port_records,
    ):
        super().__init__(parent)
        self.transport = transport or MotorSerialTransport(self)
        self.state_store = state_store
        self.port_provider = port_provider
        self._candidates: tuple[MotorPortCandidate, ...] = ()
        self._probe_candidates: tuple[MotorPortCandidate, ...] = ()
        self._probe_index = -1
        self._probing = False
        self._confirmed_candidate: MotorPortCandidate | None = None
        self._connected = False
        self._status = _unknown_status()
        self._active_motion: _ActiveMotion | object | None = None
        self._restore_pending = 0
        self._speeds = {
            Axis.X: MOTOR_SPEED_DEFAULT_HZ,
            Axis.Y: MOTOR_SPEED_DEFAULT_HZ,
            Axis.Z: MOTOR_SPEED_DEFAULT_HZ,
        }

        self._motion_poll_timer = QtCore.QTimer(self)
        self._motion_poll_timer.setSingleShot(True)
        self._motion_poll_timer.timeout.connect(self._poll_motion)

        self.transport.connection_changed.connect(
            self._on_transport_connection
        )
        self.transport.command_completed.connect(self._on_command_completed)
        self.transport.command_failed.connect(self._on_command_failed)
        self.transport.diagnostic_event.connect(self.diagnostic_event)

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def device_id(self) -> str:
        return (
            self._confirmed_candidate.stable_id
            if self._confirmed_candidate is not None
            else ""
        )

    @property
    def status(self) -> MotorStatus:
        return self._status

    @property
    def motion_active(self) -> bool:
        return self._active_motion is not None

    def discover(
        self,
        excluded_ports: set[str] | frozenset[str] = frozenset(),
    ) -> tuple[MotorPortCandidate, ...]:
        self._candidates = find_motor_candidates(
            self.port_provider(),
            excluded_ports=excluded_ports,
        )
        self.candidates_changed.emit(self._candidates)
        return self._candidates

    def connect_auto(
        self,
        excluded_ports: set[str] | frozenset[str] = frozenset(),
    ) -> bool:
        candidates = self.discover(excluded_ports)
        if not candidates:
            self.operation_failed.emit("未发现可探测的电机串口")
            return False
        self._connected = False
        self._confirmed_candidate = None
        self._probe_candidates = candidates
        self._probe_index = -1
        self._probing = True
        self._try_next_candidate()
        return True

    def _try_next_candidate(self):
        self.transport.disconnect_port()
        self._probe_index += 1
        if self._probe_index >= len(self._probe_candidates):
            self._probing = False
            self.operation_failed.emit(
                "候选串口均未通过电机 ID? 协议握手"
            )
            self.connection_changed.emit(False, "未识别到电机控制器")
            return
        candidate = self._probe_candidates[self._probe_index]
        self.transport.connect_port(candidate.port_name, 115_200)

    def disconnect(self):
        if self._active_motion is not None and self.device_id:
            self.state_store.invalidate(
                self.device_id, "disconnected_during_motion"
            )
        self._motion_poll_timer.stop()
        self._active_motion = None
        self._probing = False
        self._connected = False
        self._confirmed_candidate = None
        self._status = _unknown_status()
        self.transport.disconnect_port()
        self.connection_changed.emit(False, "")
        self.status_changed.emit(self._status)

    @Slot(bool, str)
    def _on_transport_connection(self, connected: bool, detail: str):
        if connected and self._probing:
            self.transport.send_command(
                build_id_command(),
                tag=("probe", self._probe_index),
                timeout_ms=500,
            )
            return
        if connected:
            return
        if self._probing:
            self._try_next_candidate()
            return
        if self._connected:
            if self._active_motion is not None and self.device_id:
                self.state_store.invalidate(
                    self.device_id, "connection_lost_during_motion"
                )
            self._connected = False
            self._active_motion = None
            self._motion_poll_timer.stop()
            self.connection_changed.emit(False, detail)

    def _confirm_candidate(self):
        candidate = self._probe_candidates[self._probe_index]
        self._confirmed_candidate = replace(candidate, confirmed=True)
        self._probing = False
        self._connected = True
        self.connection_changed.emit(True, candidate.port_name)

        saved = self.state_store.load(candidate.stable_id)
        if saved is not None and saved.trusted:
            self._restore_pending = 3
            for axis, position in (
                (Axis.X, saved.position.x_mm),
                (Axis.Y, saved.position.y_mm),
                (Axis.Z, saved.position.z_mm),
            ):
                self.transport.send_command(
                    build_position_set_command(axis, position),
                    tag=("restore", axis),
                )
        else:
            self.query_status(context="connected")

    def query_status(self, *, context="manual") -> bool:
        if not self._connected:
            return False
        return self.transport.send_command(
            build_status_command(),
            tag=("status", context),
            timeout_ms=1000,
        )

    @staticmethod
    def _flag(fields: dict[str, str], key: str) -> bool:
        return fields.get(key, "0") == "1"

    @classmethod
    def _parse_status(cls, response: MotorResponse) -> MotorStatus:
        if not response.ok or response.kind != "STATUS":
            raise ValueError("不是有效的电机 STATUS 响应")
        fields = response.fields
        global_fault = cls._flag(fields, "FAULT")
        axes = []
        for axis in (Axis.X, Axis.Y, Axis.Z):
            prefix = axis.value
            valid = cls._flag(fields, f"{prefix}_VALID")
            raw_position = fields.get(f"{prefix}_POS")
            position = float(raw_position) if raw_position is not None else None
            if position is not None:
                position = min(MOTOR_TRAVEL_MM, max(0.0, position))
            axes.append(
                MotorAxisStatus(
                    axis=axis,
                    position_mm=position,
                    position_valid=valid,
                    moving=cls._flag(fields, f"{prefix}_MOVING"),
                    zero_limit_active=cls._flag(
                        fields, f"{prefix}_LIMIT"
                    ),
                    fault_latched=global_fault,
                    stop_reason=fields.get(f"{prefix}_STOP", "NONE"),
                    home_state=fields.get(f"{prefix}_HOME", "IDLE"),
                )
            )
        return MotorStatus(*axes)

    def _status_axis(self, axis: Axis) -> MotorAxisStatus:
        return {
            Axis.X: self._status.x,
            Axis.Y: self._status.y,
            Axis.Z: self._status.z,
        }[Axis(axis)]

    def _position_for_store(self) -> Position | None:
        if not (
            self._status.x.position_valid
            and self._status.y.position_valid
        ):
            return None
        return Position(
            self._status.x.position_mm,
            self._status.y.position_mm,
            (
                self._status.z.position_mm
                if self._status.z.position_valid
                else 0.0
            ),
        )

    def set_speed(self, axis: Axis, speed_hz: int) -> bool:
        if not self._connected:
            self.operation_failed.emit("电机未连接")
            return False
        command = build_speed_command(axis, speed_hz)
        return self.transport.send_command(
            command,
            tag=("speed", Axis(axis), int(speed_hz)),
        )

    def set_position(self, axis: Axis, position_mm: float) -> bool:
        if not self._connected or self._active_motion is not None:
            self.operation_failed.emit("电机未连接或正在运动")
            return False
        return self.transport.send_command(
            build_position_set_command(axis, position_mm),
            tag=("set_position", Axis(axis), float(position_mm)),
        )

    def clear_faults(self) -> bool:
        if not self._connected:
            return False
        return self.transport.send_command(
            build_clear_fault_command(),
            tag=("clear_faults",),
        )

    def move_relative(
        self,
        axis: Axis,
        distance_mm: float,
        direction: Direction,
    ) -> str | None:
        axis = Axis(axis)
        direction = Direction(direction)
        if not self._connected or self._active_motion is not None:
            self.operation_failed.emit("电机未连接或已有运动正在执行")
            return None
        axis_status = self._status_axis(axis)
        if not axis_status.position_valid or axis_status.position_mm is None:
            self.operation_failed.emit(
                f"{axis.value} 轴软件坐标不可信，请先确认坐标或机械回零"
            )
            return None
        target = axis_status.position_mm + direction.sign * distance_mm
        if not 0.0 <= target <= MOTOR_TRAVEL_MM:
            self.operation_failed.emit(
                f"{axis.value} 轴目标 {target:.3f} mm 超出 0–15 mm"
            )
            return None
        command = build_move_command(axis, distance_mm, direction)
        operation_id = uuid.uuid4().hex
        duration = distance_mm * 640.0 / self._speeds[axis]
        self._active_motion = _ActiveMotion(
            operation_id=operation_id,
            kind="move",
            axis=axis,
            target_mm=target,
            deadline=time.monotonic() + max(5.0, duration * 3.0 + 2.0),
        )
        self.state_store.begin_motion(self.device_id)
        self.motion_started.emit(operation_id)
        self.transport.send_command(
            command,
            tag=("move_ack", operation_id),
            timeout_ms=1000,
        )
        return operation_id

    def return_axis_to_zero(self, axis: Axis) -> str | None:
        axis = Axis(axis)
        status = self._status_axis(axis)
        if not status.position_valid or status.position_mm is None:
            self.operation_failed.emit(f"{axis.value} 轴坐标不可信")
            return None
        if status.position_mm <= 0.0005:
            self.operation_failed.emit(f"{axis.value} 轴已在软件零点")
            return None
        return self.move_relative(
            axis,
            status.position_mm,
            Direction.NEGATIVE,
        )

    def home(self, axis: Axis | None = None) -> str | None:
        if not self._connected or self._active_motion is not None:
            self.operation_failed.emit("电机未连接或已有运动正在执行")
            return None
        operation_id = uuid.uuid4().hex
        self._active_motion = _ActiveMotion(
            operation_id=operation_id,
            kind="home",
            axis=Axis(axis) if axis is not None else None,
            target_mm=0.0,
            deadline=time.monotonic() + 50.0,
        )
        self.state_store.begin_motion(self.device_id)
        self.motion_started.emit(operation_id)
        self.transport.send_command(
            build_home_command(axis),
            tag=("home_ack", operation_id),
            timeout_ms=1000,
        )
        return operation_id

    def stop(self):
        active = self._active_motion
        self._motion_poll_timer.stop()
        self._active_motion = None
        if self.device_id and active is not None:
            self.state_store.invalidate(self.device_id, "user_stop")
        self.transport.send_emergency(
            build_stop_command(), reason="emergency stop"
        )
        operation_id = getattr(active, "operation_id", "")
        if operation_id:
            self.motion_finished.emit(operation_id, False, "user_stop")

    def _poll_motion(self):
        motion = self._active_motion
        if not isinstance(motion, _ActiveMotion) or not self._connected:
            return
        if time.monotonic() > motion.deadline:
            self._fail_motion("motion_timeout", emergency=True)
            return
        if self.transport.busy:
            self._motion_poll_timer.start(40)
            return
        self.transport.send_command(
            build_status_command(),
            tag=("motion_status", motion.operation_id),
            timeout_ms=1000,
        )

    def _schedule_motion_poll(self):
        self._motion_poll_timer.start(40)

    def _handle_motion_status(self, response: MotorResponse):
        motion = self._active_motion
        if not isinstance(motion, _ActiveMotion):
            return
        try:
            self._status = self._parse_status(response)
        except (TypeError, ValueError) as exc:
            self._fail_motion(f"bad_status: {exc}", emergency=True)
            return
        self.status_changed.emit(self._status)
        if self._status.fault_latched:
            self._fail_motion("motor_fault", emergency=True)
            return
        if self._status.moving:
            self._schedule_motion_poll()
            return

        if motion.kind == "home":
            targets = (
                (self._status.x, self._status.y, self._status.z)
                if motion.axis is None
                else (self._status_axis(motion.axis),)
            )
            if any(status.home_state == "FAILED" for status in targets):
                self._fail_motion("home_failed", emergency=False)
                return
            if not all(status.home_state == "DONE" for status in targets):
                self._schedule_motion_poll()
                return
        else:
            axis_status = self._status_axis(motion.axis)
            expected_limit = (
                motion.target_mm is not None
                and motion.target_mm <= 0.0005
                and axis_status.zero_limit_active
                and axis_status.stop_reason == "LIMIT"
            )
            if axis_status.stop_reason != "DONE" and not expected_limit:
                self._fail_motion(
                    f"unexpected_stop:{axis_status.stop_reason}",
                    emergency=False,
                )
                return

        position = self._position_for_store()
        if position is None:
            self._fail_motion("position_invalid", emergency=False)
            return
        operation_id = motion.operation_id
        self.state_store.confirm_position(self.device_id, position)
        self._active_motion = None
        self.motion_finished.emit(operation_id, True, "completed")

    def _fail_motion(self, reason: str, *, emergency: bool):
        motion = self._active_motion
        self._active_motion = None
        self._motion_poll_timer.stop()
        if self.device_id:
            self.state_store.invalidate(self.device_id, reason)
        if emergency:
            self.transport.send_emergency(
                build_stop_command(), reason=reason
            )
        operation_id = getattr(motion, "operation_id", "")
        if operation_id:
            self.motion_finished.emit(operation_id, False, reason)
        self.operation_failed.emit(reason)

    @Slot(object, object)
    def _on_command_completed(self, tag, response: MotorResponse):
        kind = tag[0] if isinstance(tag, tuple) and tag else ""
        if kind == "probe":
            if response.ok and is_motor_identity(response.fields):
                self._confirm_candidate()
            else:
                self._try_next_candidate()
            return
        if not response.ok:
            if kind in ("move_ack", "home_ack", "motion_status"):
                self._fail_motion(response.error or "motor_error", emergency=True)
            else:
                self.operation_failed.emit(response.error or "motor_error")
            return
        if kind == "restore":
            self._restore_pending = max(0, self._restore_pending - 1)
            if self._restore_pending == 0:
                self.query_status(context="restore")
            return
        if kind in ("status", "sync_status"):
            try:
                self._status = self._parse_status(response)
            except (TypeError, ValueError) as exc:
                self.operation_failed.emit(str(exc))
                return
            self.status_changed.emit(self._status)
            position = self._position_for_store()
            if position is not None and self.device_id:
                saved = self.state_store.load(self.device_id)
                if saved is not None and saved.trusted:
                    self.state_store.confirm_position(self.device_id, position)
            return
        if kind == "speed":
            self._speeds[tag[1]] = tag[2]
            return
        if kind == "set_position":
            self.query_status(context="set_position")
            return
        if kind in ("move_ack", "home_ack"):
            self._schedule_motion_poll()
            return
        if kind == "motion_status":
            self._handle_motion_status(response)

    @Slot(object, str)
    def _on_command_failed(self, tag, reason: str):
        kind = tag[0] if isinstance(tag, tuple) and tag else ""
        if kind == "probe" and self._probing:
            self._try_next_candidate()
            return
        if kind in ("move_ack", "home_ack", "motion_status"):
            self._fail_motion(reason, emergency=True)
            return
        if kind == "restore":
            if self.device_id:
                self.state_store.invalidate(
                    self.device_id, "coordinate_restore_failed"
                )
        self.operation_failed.emit(str(reason))

    def shutdown(self):
        if self._active_motion is not None:
            self.stop()
        self.transport.shutdown()
