"""LK-MD2202 child-process runtime and IPC command dispatcher."""

from __future__ import annotations

import queue
import time

from ..qt import QtCore, Slot, application_exec
from .controller import MotorController
from .process_scan_runner import MotorScanRunner
from .settings_store import MotorSettingsStore
from .transport import MotorSerialTransport


ALLOWED_COMMANDS = frozenset(
    {
        "connect_auto",
        "connect_manual",
        "disconnect",
        "update_host_settings",
        "apply_configuration",
        "set_speed",
        "release_stall",
        "move_relative",
        "move_scan_segment",
        "prepare_scan_round",
        "verify_scan_round",
        "set_scan_active",
        "set_position",
        "return_axis_to_zero",
        "home",
        "clear_faults",
        "query_status",
        "stop",
        "start_scan_path",
    }
)

_DROPPABLE_EVENTS = frozenset(
    {"snapshot", "status_changed", "diagnostic_event", "candidates_changed"}
)


class MotorProcessRuntime(QtCore.QObject):
    """Owns the controller, path runner and fail-closed event channel."""

    def __init__(
        self,
        control,
        event_queue,
        *,
        controller,
        application,
        parent=None,
    ):
        super().__init__(parent)
        self.control = control
        self.events = event_queue
        self.controller = controller
        self.application = application
        self.runner = MotorScanRunner(controller, self)
        self._closing = False
        self._failed_closed = False
        self._dropped_events = 0
        self._exit_code = 0
        self._shutdown_deadline = 0.0
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(2)
        self._timer.timeout.connect(self._poll_control)
        self._shutdown_timer = QtCore.QTimer(self)
        self._shutdown_timer.setInterval(25)
        self._shutdown_timer.timeout.connect(self._check_shutdown_ready)
        self._connect_signals()

    def start(self) -> None:
        if self.control is not None:
            self._timer.start()
        self._emit("process_started")
        self._emit_snapshot()

    def _connect_signals(self) -> None:
        mappings = (
            (self.controller.candidates_changed, "candidates_changed"),
            (self.controller.connection_changed, "connection_changed"),
            (self.controller.status_changed, "status_changed"),
            (self.controller.configuration_changed, "configuration_changed"),
            (self.controller.operation_failed, "operation_failed"),
            (self.controller.diagnostic_event, "diagnostic_event"),
            (self.controller.motion_started, "motion_started"),
            (self.controller.motion_finished, "motion_finished"),
            (
                self.controller.configuration_apply_finished,
                "configuration_apply_finished",
            ),
            (self.controller.speed_applied, "speed_applied"),
            (self.controller.scan_round_prepared, "scan_round_prepared"),
            (self.controller.scan_round_verified, "scan_round_verified"),
        )
        for signal, kind in mappings:
            signal.connect(
                lambda *values, event_kind=kind: self._forward_signal(
                    event_kind, *values
                )
            )
        self.runner.path_started.connect(
            lambda path_id, count, returning: self._emit(
                "path_started", path_id, count, returning
            )
        )
        self.runner.segment_started.connect(
            lambda payload: self._emit("segment_started", dict(payload))
        )
        self.runner.segment_finished.connect(
            lambda payload: self._emit("segment_finished", dict(payload))
        )
        self.runner.path_finished.connect(
            lambda path_id, success, reason: self._emit(
                "path_finished", path_id, success, reason
            )
        )

    def _forward_signal(self, kind: str, *values) -> None:
        self._emit(kind, *values)
        if kind in {
            "connection_changed",
            "configuration_changed",
            "status_changed",
            "motion_started",
            "motion_finished",
            "configuration_apply_finished",
            "speed_applied",
            "scan_round_prepared",
            "scan_round_verified",
        }:
            self._emit_snapshot()

    def _snapshot(self) -> dict:
        return {
            "connected": bool(self.controller.connected),
            "device_id": str(self.controller.device_id),
            "status": self.controller.status,
            "configuration": self.controller.configuration,
            "host_settings": self.controller.host_settings,
            "motion_active": bool(self.controller.motion_active),
            "scan_active": bool(self.controller.scan_active),
            "safety_locked": bool(self.controller.safety_locked),
            "mechanics_valid": bool(self.controller.mechanics_valid),
            "dropped_events": int(self._dropped_events),
        }

    def _emit_snapshot(self) -> None:
        self._emit("snapshot", self._snapshot())

    def _emit(self, kind: str, *values) -> bool:
        if self._failed_closed:
            return False
        event = (str(kind), *values)
        try:
            self.events.put_nowait(event)
            return True
        except queue.Full:
            if kind in _DROPPABLE_EVENTS:
                self._dropped_events += 1
                return False
            self._fail_closed("motor_event_queue_full")
            return False

    def _fail_closed(self, reason: str) -> None:
        if self._failed_closed:
            return
        self._failed_closed = True
        self._timer.stop()
        self._exit_code = 2
        try:
            if self.runner.active:
                self.runner.stop(str(reason))
            else:
                self.controller.stop()
        except Exception:
            pass
        self._begin_shutdown_wait()

    def _poll_control(self) -> None:
        if self.control is None or self._closing:
            return
        try:
            while self.control.poll():
                self.handle_message(self.control.recv())
        except (EOFError, OSError):
            self._fail_closed("motor_control_channel_closed")

    def handle_message(self, message) -> None:
        if not isinstance(message, tuple) or not message:
            self._emit("protocol_error", "invalid_motor_message")
            return
        kind = message[0]
        if kind == "shutdown":
            self.close()
            return
        if kind != "command" or len(message) != 5:
            self._emit("protocol_error", "invalid_motor_message")
            return
        _, sequence, name, args, kwargs = message
        name = str(name)
        if name not in ALLOWED_COMMANDS:
            self._emit(
                "command_result",
                int(sequence),
                name,
                False,
                "unsupported_motor_command",
            )
            return
        try:
            if name == "start_scan_path":
                result = self.runner.start_path(*args, **kwargs)
                if not result:
                    path_id = str(args[0]) if args else ""
                    self._emit(
                        "path_finished",
                        path_id,
                        False,
                        "scan_path_not_accepted",
                    )
            elif name == "stop":
                result = (
                    self.runner.stop("user_stop")
                    if self.runner.active
                    else self.controller.stop()
                )
            else:
                result = getattr(self.controller, name)(*args, **kwargs)
            if name == "prepare_scan_round" and result is False:
                self._emit(
                    "scan_round_prepared",
                    False,
                    "scan_round_prepare_not_accepted",
                )
            elif name == "verify_scan_round" and result is False:
                self._emit(
                    "scan_round_verified",
                    False,
                    "scan_round_verify_not_accepted",
                )
            self._emit("command_result", int(sequence), name, bool(result))
            self._emit_snapshot()
        except Exception as exc:
            self._emit(
                "command_result",
                int(sequence),
                name,
                False,
                f"{type(exc).__name__}:{exc}",
            )
            self._emit("operation_failed", f"电机命令 {name} 执行异常：{exc}")
            self._emit_snapshot()

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._timer.stop()
        wait_for_stop = False
        try:
            if self.runner.active:
                self.runner.stop("process_shutdown")
                wait_for_stop = True
            elif self.controller.motion_active:
                self.controller.stop()
                wait_for_stop = True
            if self.controller.scan_active:
                self.controller.set_scan_active(False)
        except Exception:
            wait_for_stop = False
        if wait_for_stop:
            self._begin_shutdown_wait()
        else:
            self._finish_close()

    def _begin_shutdown_wait(self) -> None:
        self._shutdown_deadline = time.monotonic() + 2.2
        self._shutdown_timer.start()

    @Slot()
    def _check_shutdown_ready(self) -> None:
        transport = getattr(self.controller, "transport", None)
        busy = bool(getattr(transport, "busy", False))
        if busy and time.monotonic() < self._shutdown_deadline:
            return
        self._finish_close()

    def _finish_close(self) -> None:
        self._shutdown_timer.stop()
        try:
            self.controller.shutdown()
        except Exception:
            pass
        self._emit("process_stopped")
        self.application.exit(int(self._exit_code))


def motor_process_main(control, event_queue, settings_path) -> None:
    application = QtCore.QCoreApplication(["zgcai-motor-process"])
    transport = MotorSerialTransport(own_thread=False)
    controller = MotorController(
        transport=transport,
        settings_store=MotorSettingsStore(settings_path),
    )
    runtime = MotorProcessRuntime(
        control,
        event_queue,
        controller=controller,
        application=application,
    )
    runtime.start()
    try:
        application_exec(application)
    finally:
        if not runtime._closing:
            try:
                runtime.close()
            except Exception:
                pass
        try:
            control.close()
        except Exception:
            pass
