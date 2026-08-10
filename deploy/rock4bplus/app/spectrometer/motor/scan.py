"""Deterministic integer-pulse raster scan planning."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math

from .models import MOTOR_PULSES_PER_MM, MOTOR_TRAVEL_MM, Axis, Direction, Position


MAX_SCAN_MOVES = 200_000


@dataclass(frozen=True)
class ScanParameters:
    x_mm: float
    y_mm: float
    line_count: int
    scan_count: int
    x_steps: int = 1
    y_steps: int = 1
    dwell_seconds: float = 0.0


@dataclass(frozen=True)
class ScanMove:
    axis: Axis
    direction: Direction
    pulses: int
    dwell_after_seconds: float
    acquiring: bool

    @property
    def distance_mm(self):
        return self.pulses / MOTOR_PULSES_PER_MM

    @property
    def signed_distance_mm(self):
        return self.distance_mm * self.direction.sign


@dataclass(frozen=True)
class ScanRound:
    index: int
    start: Position
    scan_moves: tuple[ScanMove, ...]
    scan_end: Position
    return_moves: tuple[ScanMove, ...]
    end: Position


@dataclass(frozen=True)
class ScanPlan:
    parameters: ScanParameters
    start: Position
    calibrated_start: bool
    rounds: tuple[ScanRound, ...]


def _validate(parameters, start, calibrated):
    for name, value in (("x_mm", parameters.x_mm), ("y_mm", parameters.y_mm)):
        if not math.isfinite(value) or value <= 0 or value > MOTOR_TRAVEL_MM:
            raise ValueError(f"{name} must be within 0..15 mm")
    for name in ("line_count", "scan_count", "x_steps", "y_steps"):
        value = getattr(parameters, name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if parameters.y_mm * parameters.line_count > MOTOR_TRAVEL_MM + 1e-9:
        raise ValueError("Y scan matrix exceeds 15 mm travel")
    move_count = parameters.scan_count * (
        (parameters.line_count + 1) * parameters.x_steps
        + parameters.line_count * parameters.y_steps
    )
    if move_count > MAX_SCAN_MOVES:
        raise ValueError(
            f"scan plan contains {move_count} moves; maximum is {MAX_SCAN_MOVES}"
        )
    if not math.isfinite(parameters.dwell_seconds) or parameters.dwell_seconds < 0:
        raise ValueError("dwell_seconds must be non-negative")
    x_pulses, y_pulses = round(parameters.x_mm * MOTOR_PULSES_PER_MM), round(parameters.y_mm * MOTOR_PULSES_PER_MM)
    if x_pulses < parameters.x_steps or y_pulses < parameters.y_steps:
        raise ValueError("each scan substep must contain at least one pulse")
    if calibrated:
        if start.x_mm + x_pulses / MOTOR_PULSES_PER_MM > MOTOR_TRAVEL_MM + 1e-9:
            raise ValueError("X scan path exceeds 15 mm travel")
        if start.y_mm + y_pulses * parameters.line_count / MOTOR_PULSES_PER_MM > MOTOR_TRAVEL_MM + 1e-9:
            raise ValueError("Y scan path exceeds 15 mm travel")


def _split_pulses(total, count):
    quotient, remainder = divmod(total, count)
    return tuple(quotient + (1 if index < remainder else 0) for index in range(count))


def _substeps(axis, direction, total_pulses, count, dwell):
    return [ScanMove(axis, direction, pulses, dwell, True) for pulses in _split_pulses(total_pulses, count)]


def _apply(position, move):
    delta = move.signed_distance_mm
    if move.axis is Axis.X:
        return Position(round(position.x_mm + delta, 9), position.y_mm)
    return Position(position.x_mm, round(position.y_mm + delta, 9))


def _build_round(parameters, start, index):
    x_pulses = round(parameters.x_mm * MOTOR_PULSES_PER_MM)
    y_pulses = round(parameters.y_mm * MOTOR_PULSES_PER_MM)
    position = start
    moves = []
    x_direction = Direction.POSITIVE
    for _ in range(parameters.line_count):
        for move in _substeps(Axis.X, x_direction, x_pulses, parameters.x_steps, parameters.dwell_seconds):
            moves.append(move); position = _apply(position, move)
        for move in _substeps(Axis.Y, Direction.POSITIVE, y_pulses, parameters.y_steps, parameters.dwell_seconds):
            moves.append(move); position = _apply(position, move)
        x_direction = Direction.NEGATIVE if x_direction is Direction.POSITIVE else Direction.POSITIVE
    for move in _substeps(Axis.X, x_direction, x_pulses, parameters.x_steps, parameters.dwell_seconds):
        moves.append(move); position = _apply(position, move)
    if moves:
        moves[-1] = replace(moves[-1], dwell_after_seconds=0.0)
    scan_end = position
    returns = []
    x_delta = round((start.x_mm - position.x_mm) * MOTOR_PULSES_PER_MM)
    if x_delta:
        move = ScanMove(Axis.X, Direction.POSITIVE if x_delta > 0 else Direction.NEGATIVE, abs(x_delta), 0, False)
        returns.append(move); position = _apply(position, move)
    y_delta = round((start.y_mm - position.y_mm) * MOTOR_PULSES_PER_MM)
    if y_delta:
        move = ScanMove(Axis.Y, Direction.POSITIVE if y_delta > 0 else Direction.NEGATIVE, abs(y_delta), 0, False)
        returns.append(move); position = _apply(position, move)
    return ScanRound(index, start, tuple(moves), scan_end, tuple(returns), position)


def build_scan_plan(parameters, *, start=None):
    calibrated = start is not None
    start = Position(0, 0) if start is None else start
    _validate(parameters, start, calibrated)
    rounds = tuple(_build_round(parameters, start, index + 1) for index in range(parameters.scan_count))
    return ScanPlan(parameters, start, calibrated, rounds)
