"""Run a frozen motor scan path inside one Qt event loop."""

from __future__ import annotations

import time

from ..qt import QtCore, Signal, Slot
from .models import Axis, Direction
from .scan import ScanMove


class MotorScanRunner(QtCore.QObject):
    """Chains scan segments without a GUI-process round trip."""

    path_started = Signal(str, int, bool)
    segment_started = Signal(object)
    segment_finished = Signal(object)
    path_finished = Signal(str, bool, str)

    def __init__(self, motor_controller, parent=None):
        super().__init__(parent)
        self.motor = motor_controller
        self._path_id = ""
        self._moves: tuple[ScanMove, ...] = ()
        self._returning = False
        self._index = 0
        self._operation_id = ""
        self._active = False
        self._dwell_timer = QtCore.QTimer(self)
        self._dwell_timer.setSingleShot(True)
        self._dwell_timer.timeout.connect(self._after_dwell)
        self.motor.motion_finished.connect(self._on_motion_finished)

    @property
    def active(self) -> bool:
        return self._active

    @property
    def path_id(self) -> str:
        return self._path_id

    def start_path(self, path_id, moves, *, returning: bool) -> bool:
        if self._active:
            return False
        path_id = str(path_id)
        if not path_id:
            return False
        try:
            frozen_moves = tuple(
                ScanMove(
                    Axis(move.axis),
                    Direction(move.direction),
                    int(move.pulses),
                    float(move.dwell_after_seconds),
                    bool(move.acquiring),
                    logical_substeps=int(move.logical_substeps),
                )
                for move in moves
            )
        except (AttributeError, TypeError, ValueError):
            return False
        if any(move.pulses <= 0 for move in frozen_moves):
            return False

        self._path_id = path_id
        self._moves = frozen_moves
        self._returning = bool(returning)
        self._index = 0
        self._operation_id = ""
        self._active = True
        self.path_started.emit(path_id, len(frozen_moves), self._returning)
        if not frozen_moves:
            self._finish(True, "completed")
            return True
        self._start_current_move()
        return self._active or self._index >= len(self._moves)

    def stop(self, reason="user_stop") -> bool:
        if not self._active:
            return False
        path_id = self._path_id
        self._reset()
        try:
            self.motor.stop()
        finally:
            self.path_finished.emit(path_id, False, str(reason))
        return True

    def _start_current_move(self) -> None:
        if not self._active:
            return
        if self._index >= len(self._moves):
            self._finish(True, "completed")
            return
        move = self._moves[self._index]
        predictive = not self._returning and self._index < len(self._moves) - 1
        scheduled_ns = time.monotonic_ns()
        try:
            operation_id = self.motor.move_scan_segment(
                move.axis,
                move.pulses,
                move.direction,
                predictive=predictive,
                returning=self._returning,
            )
        except Exception as exc:
            self._finish(False, f"scan_segment_exception:{exc}")
            return
        if not operation_id:
            self._finish(False, "scan_command_not_accepted")
            return
        self._operation_id = str(operation_id)
        self.segment_started.emit(
            {
                "path_id": self._path_id,
                "segment_number": self._index + 1,
                "segment_total": len(self._moves),
                "operation_id": self._operation_id,
                "axis": move.axis.value,
                "direction": move.direction.value,
                "pulses": int(move.pulses),
                "logical_substeps": int(move.logical_substeps),
                "coalesced": bool(move.logical_substeps > 1),
                "predictive": bool(predictive),
                "returning": self._returning,
                "scheduled_monotonic_ns": scheduled_ns,
            }
        )

    @Slot(str, bool, str)
    def _on_motion_finished(
        self, operation_id: str, success: bool, reason: str
    ) -> None:
        if not self._active or str(operation_id) != self._operation_id:
            return
        move = self._moves[self._index]
        self._operation_id = ""
        self.segment_finished.emit(
            {
                "path_id": self._path_id,
                "segment_number": self._index + 1,
                "segment_total": len(self._moves),
                "operation_id": str(operation_id),
                "axis": move.axis.value,
                "direction": move.direction.value,
                "pulses": int(move.pulses),
                "logical_substeps": int(move.logical_substeps),
                "returning": self._returning,
                "success": bool(success),
                "reason": str(reason),
                "completed_monotonic_ns": time.monotonic_ns(),
            }
        )
        if not success:
            self._finish(False, str(reason) or "scan_motion_failed")
            return
        self._index += 1
        if self._index >= len(self._moves):
            self._finish(True, "completed")
            return
        if move.dwell_after_seconds > 0.0:
            self._dwell_timer.start(
                max(1, round(move.dwell_after_seconds * 1000))
            )
            return
        self._start_current_move()

    @Slot()
    def _after_dwell(self) -> None:
        if self._active and not self._operation_id:
            self._start_current_move()

    def _finish(self, success: bool, reason: str) -> None:
        if not self._active:
            return
        path_id = self._path_id
        self._reset()
        self.path_finished.emit(path_id, bool(success), str(reason))

    def _reset(self) -> None:
        self._dwell_timer.stop()
        self._path_id = ""
        self._moves = ()
        self._returning = False
        self._index = 0
        self._operation_id = ""
        self._active = False
