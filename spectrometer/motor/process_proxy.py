"""Qt GUI facade for the dedicated LK-MD2202 control process."""

from __future__ import annotations

import multiprocessing
from pathlib import Path
import queue

from ..qt import QtCore, Signal, Slot
from .discovery import available_port_records, find_motor_candidates
from .models import Axis, Direction, MotorAxisStatus, MotorStatus
from .process_worker import motor_process_main
from .settings_store import HostMotorSettings, MotorSettingsStore


def _unknown_status() -> MotorStatus:
    return MotorStatus(
        MotorAxisStatus(Axis.X, None, None, False, False, None),
        MotorAxisStatus(Axis.Y, None, None, False, False, None),
    )


class MotorProcessProxy(QtCore.QObject):
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
    path_started = Signal(str, int, bool)
    path_segment_started = Signal(object)
    path_segment_finished = Signal(object)
    path_finished = Signal(str, bool, str)
    process_started = Signal(int)
    process_stopped = Signal(str)

    def __init__(
        self,
        parent=None,
        *,
        settings_path="motor-settings.json",
        port_provider=available_port_records,
        autostart=True,
    ):
        super().__init__(parent)
        self.settings_path = Path(settings_path)
        self.port_provider = port_provider
        self.host_settings = MotorSettingsStore(self.settings_path).load()
        self._status = _unknown_status()
        self._configuration = None
        self._connected = False
        self._device_id = ""
        self._motion_active = False
        self._scan_active = False
        self._safety_locked = False
        self._mechanics_valid = False
        self._candidates = ()
        self._sequence = 0
        self._context = multiprocessing.get_context("spawn")
        self._control = None
        self._events = None
        self._process = None
        self._closing = False
        self._exit_reported = False
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(5)
        self._timer.timeout.connect(self._poll)
        if autostart:
            self._start_process()

    @property
    def connected(self):
        return self._connected

    @property
    def device_id(self):
        return self._device_id

    @property
    def status(self):
        return self._status

    @property
    def motion_active(self):
        return self._motion_active

    @property
    def safety_locked(self):
        return self._safety_locked

    @property
    def configuration(self):
        return self._configuration

    @property
    def mechanics_valid(self):
        return self._mechanics_valid

    @property
    def scan_active(self):
        return self._scan_active

    @property
    def process_id(self):
        return self._process.pid if self._process is not None else None

    def _start_process(self) -> bool:
        if self._process is not None:
            return self._process.is_alive()
        parent, child = self._context.Pipe()
        events = self._context.Queue(maxsize=512)
        process = self._context.Process(
            target=motor_process_main,
            args=(child, events, str(self.settings_path)),
            name="zgcai-motor",
            daemon=True,
        )
        try:
            process.start()
        except Exception as exc:
            child.close()
            parent.close()
            events.close()
            self._handle_process_exit(f"motor_process_start_failed:{exc}")
            return False
        child.close()
        self._control = parent
        self._events = events
        self._process = process
        self._exit_reported = False
        self._timer.start()
        return True

    def discover(self, excluded_ports=frozenset()):
        self._candidates = find_motor_candidates(
            self.port_provider(),
            excluded_ports=excluded_ports,
            preferred_port=self.host_settings.port_name,
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
        return bool(
            self._send_command("connect_auto", frozenset(excluded_ports))
        )

    def connect_manual(self, port_name: str, address: int, baud_rate: int):
        return bool(
            self._send_command(
                "connect_manual", str(port_name), int(address), int(baud_rate)
            )
        )

    def disconnect(self):
        self._send_command("disconnect")

    def update_host_settings(self, settings):
        if not isinstance(settings, HostMotorSettings):
            raise TypeError("invalid motor host settings")
        return bool(self._send_command("update_host_settings", settings))

    def apply_configuration(self, desired, host_settings):
        return bool(
            self._send_command("apply_configuration", desired, host_settings)
        )

    def set_speed(self, axis, speed_hz):
        return bool(self._send_command("set_speed", Axis(axis), int(speed_hz)))

    def release_stall(self, axis, signed_speed_pps):
        return self._operation_command(
            "release_stall", Axis(axis), int(signed_speed_pps)
        )

    def move_relative(self, axis, distance_mm, direction):
        return self._operation_command(
            "move_relative",
            Axis(axis),
            float(distance_mm),
            Direction(direction),
        )

    def move_scan_segment(
        self,
        axis,
        pulses,
        direction,
        *,
        predictive=True,
        returning=False,
    ):
        return self._operation_command(
            "move_scan_segment",
            Axis(axis),
            int(pulses),
            Direction(direction),
            predictive=bool(predictive),
            returning=bool(returning),
        )

    def set_scan_active(self, active: bool):
        self._scan_active = bool(active)
        return bool(self._send_command("set_scan_active", bool(active)))

    def prepare_scan_round(self):
        return bool(self._send_command("prepare_scan_round"))

    def verify_scan_round(self):
        return bool(self._send_command("verify_scan_round"))

    def start_scan_path(self, path_id, moves, *, returning):
        return bool(
            self._send_command(
                "start_scan_path",
                str(path_id),
                tuple(moves),
                returning=bool(returning),
            )
        )

    def set_position(self, axis, position_mm):
        return bool(
            self._send_command("set_position", Axis(axis), float(position_mm))
        )

    def return_axis_to_zero(self, axis):
        return self._operation_command("return_axis_to_zero", Axis(axis))

    def home(self, axis=None):
        return self._operation_command(
            "home", None if axis is None else Axis(axis)
        )

    def clear_faults(self):
        return bool(self._send_command("clear_faults"))

    def query_status(self):
        return bool(self._send_command("query_status"))

    def stop(self):
        return bool(self._send_command("stop"))

    def _operation_command(self, name, *args, **kwargs):
        sequence = self._send_command(name, *args, **kwargs)
        return f"proxy-{sequence}" if sequence else None

    def _send_command(self, name, *args, **kwargs):
        if self._control is None and not self._start_process():
            return 0
        self._sequence += 1
        sequence = self._sequence
        try:
            self._control.send(
                ("command", sequence, str(name), tuple(args), dict(kwargs))
            )
        except (BrokenPipeError, EOFError, OSError) as exc:
            self._handle_process_exit(f"motor_control_send_failed:{exc}")
            return 0
        return sequence

    @Slot()
    def _poll(self):
        if self._events is not None:
            while True:
                try:
                    event = self._events.get_nowait()
                except queue.Empty:
                    break
                self._dispatch(event)
        if (
            not self._closing
            and self._process is not None
            and not self._process.is_alive()
        ):
            self._timer.stop()
            self._handle_process_exit(
                f"motor_process_exited:{self._process.exitcode}"
            )

    def _dispatch(self, event) -> None:
        kind = event[0]
        values = event[1:]
        if kind == "snapshot":
            snapshot = dict(values[0])
            self._connected = bool(snapshot["connected"])
            self._device_id = str(snapshot["device_id"])
            self._status = snapshot["status"]
            self._configuration = snapshot["configuration"]
            self.host_settings = snapshot["host_settings"]
            self._motion_active = bool(snapshot["motion_active"])
            self._scan_active = bool(snapshot["scan_active"])
            self._safety_locked = bool(snapshot["safety_locked"])
            self._mechanics_valid = bool(snapshot["mechanics_valid"])
        elif kind == "process_started":
            self.process_started.emit(int(self.process_id or 0))
            self.diagnostic_event.emit(
                f"电机控制进程已启动：PID {int(self.process_id or 0)}"
            )
        elif kind == "process_stopped":
            self.process_stopped.emit("normal")
        elif kind == "candidates_changed":
            self._candidates = tuple(values[0])
            self.candidates_changed.emit(self._candidates)
        elif kind == "connection_changed":
            self._connected = bool(values[0])
            self.connection_changed.emit(bool(values[0]), str(values[1]))
        elif kind == "status_changed":
            self._status = values[0]
            self._safety_locked = bool(self._status.fault_latched)
            self.status_changed.emit(values[0])
        elif kind == "configuration_changed":
            self._configuration = values[0]
            self.configuration_changed.emit(values[0])
        elif kind == "operation_failed":
            self.operation_failed.emit(str(values[0]))
        elif kind == "diagnostic_event":
            self.diagnostic_event.emit(str(values[0]))
        elif kind == "motion_started":
            self._motion_active = True
            self.motion_started.emit(str(values[0]))
        elif kind == "motion_finished":
            self._motion_active = False
            self.motion_finished.emit(
                str(values[0]), bool(values[1]), str(values[2])
            )
        elif kind == "configuration_apply_finished":
            self.configuration_apply_finished.emit(
                bool(values[0]), str(values[1])
            )
        elif kind == "speed_applied":
            self.speed_applied.emit(
                values[0], int(values[1]), int(values[2]), str(values[3])
            )
        elif kind == "scan_round_prepared":
            self.scan_round_prepared.emit(bool(values[0]), str(values[1]))
        elif kind == "scan_round_verified":
            self.scan_round_verified.emit(bool(values[0]), str(values[1]))
        elif kind == "path_started":
            self.path_started.emit(str(values[0]), int(values[1]), bool(values[2]))
        elif kind == "segment_started":
            self.path_segment_started.emit(dict(values[0]))
        elif kind == "segment_finished":
            self.path_segment_finished.emit(dict(values[0]))
        elif kind == "path_finished":
            self.path_finished.emit(
                str(values[0]), bool(values[1]), str(values[2])
            )
        elif kind == "protocol_error":
            self.operation_failed.emit(f"电机进程通信错误：{values[0]}")
        elif kind == "command_result" and not bool(values[2]):
            reason = str(values[3]) if len(values) > 3 else "command_rejected"
            self.diagnostic_event.emit(
                f"电机进程命令 {values[1]} 未接受：{reason}"
            )

    def _handle_process_exit(self, reason: str) -> None:
        if self._exit_reported:
            return
        self._exit_reported = True
        self._connected = False
        self._motion_active = False
        self._scan_active = False
        self.connection_changed.emit(False, str(reason))
        self.operation_failed.emit(f"电机控制进程异常退出：{reason}")
        self.process_stopped.emit(str(reason))

    def shutdown(self):
        if self._closing:
            return
        self._closing = True
        self._timer.stop()
        if self._control is not None:
            try:
                self._control.send(("shutdown",))
            except (BrokenPipeError, EOFError, OSError):
                pass
        process = self._process
        if process is not None:
            process.join(timeout=3.0)
            if process.is_alive():
                process.terminate()
                process.join(timeout=1.0)
        if self._control is not None:
            self._control.close()
        if self._events is not None:
            self._events.close()
        self._control = None
        self._events = None
        self._process = None
