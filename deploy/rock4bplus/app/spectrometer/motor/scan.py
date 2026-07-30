"""Deterministic host-side raster scan planning."""

from __future__ import annotations

from dataclasses import dataclass
import math

from .models import (
    MINIMUM_MOVE_MM,
    MOTOR_TRAVEL_MM,
    Axis,
    Direction,
    Position,
)


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
    distance_mm: float
    dwell_after_seconds: float
    acquiring: bool

    @property
    def signed_distance_mm(self) -> float:
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
    rounds: tuple[ScanRound, ...]


def _validate_parameters(parameters: ScanParameters, start: Position):
    for name, value in (("x_mm", parameters.x_mm), ("y_mm", parameters.y_mm)):
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(f"{name} must be positive and finite")
        if value > MOTOR_TRAVEL_MM:
            raise ValueError(f"{name} exceeds the {MOTOR_TRAVEL_MM:g} mm travel")
    for name, value in (
        ("line_count", parameters.line_count),
        ("scan_count", parameters.scan_count),
        ("x_steps", parameters.x_steps),
        ("y_steps", parameters.y_steps),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if (
        not math.isfinite(parameters.dwell_seconds)
        or parameters.dwell_seconds < 0.0
    ):
        raise ValueError("dwell_seconds must be non-negative and finite")

    if parameters.x_mm / parameters.x_steps < MINIMUM_MOVE_MM:
        raise ValueError("each X substep must contain at least one motor pulse")
    if parameters.y_mm / parameters.y_steps < MINIMUM_MOVE_MM:
        raise ValueError("each Y substep must contain at least one motor pulse")
    if start.x_mm + parameters.x_mm > MOTOR_TRAVEL_MM + 1e-9:
        raise ValueError("X scan path exceeds the 15 mm software travel")
    if (
        start.y_mm + parameters.y_mm * parameters.line_count
        > MOTOR_TRAVEL_MM + 1e-9
    ):
        raise ValueError("Y scan path exceeds the 15 mm software travel")


def _position(x_mm: float, y_mm: float, z_mm: float) -> Position:
    return Position(round(x_mm, 9), round(y_mm, 9), round(z_mm, 9))


def _substeps(
    axis: Axis,
    direction: Direction,
    full_distance_mm: float,
    count: int,
    dwell_seconds: float,
) -> list[ScanMove]:
    distance = full_distance_mm / count
    return [
        ScanMove(
            axis=axis,
            direction=direction,
            distance_mm=distance,
            dwell_after_seconds=dwell_seconds,
            acquiring=True,
        )
        for _ in range(count)
    ]


def _apply(position: Position, move: ScanMove) -> Position:
    delta = move.signed_distance_mm
    if move.axis is Axis.X:
        return _position(
            position.x_mm + delta,
            position.y_mm,
            position.z_mm,
        )
    if move.axis is Axis.Y:
        return _position(
            position.x_mm,
            position.y_mm + delta,
            position.z_mm,
        )
    return _position(
        position.x_mm,
        position.y_mm,
        position.z_mm + delta,
    )


def _build_round(
    parameters: ScanParameters,
    start: Position,
    index: int,
) -> ScanRound:
    position = start
    scan_moves: list[ScanMove] = []
    x_direction = Direction.POSITIVE

    for _line_index in range(parameters.line_count):
        for move in _substeps(
            Axis.X,
            x_direction,
            parameters.x_mm,
            parameters.x_steps,
            parameters.dwell_seconds,
        ):
            scan_moves.append(move)
            position = _apply(position, move)
        for move in _substeps(
            Axis.Y,
            Direction.POSITIVE,
            parameters.y_mm,
            parameters.y_steps,
            parameters.dwell_seconds,
        ):
            scan_moves.append(move)
            position = _apply(position, move)
        x_direction = (
            Direction.NEGATIVE
            if x_direction is Direction.POSITIVE
            else Direction.POSITIVE
        )

    for move in _substeps(
        Axis.X,
        x_direction,
        parameters.x_mm,
        parameters.x_steps,
        parameters.dwell_seconds,
    ):
        scan_moves.append(move)
        position = _apply(position, move)
    scan_end = position

    return_moves: list[ScanMove] = []
    x_delta = start.x_mm - position.x_mm
    if abs(x_delta) >= MINIMUM_MOVE_MM:
        move = ScanMove(
            axis=Axis.X,
            direction=(
                Direction.POSITIVE if x_delta > 0 else Direction.NEGATIVE
            ),
            distance_mm=abs(x_delta),
            dwell_after_seconds=0.0,
            acquiring=False,
        )
        return_moves.append(move)
        position = _apply(position, move)
    y_delta = start.y_mm - position.y_mm
    if abs(y_delta) >= MINIMUM_MOVE_MM:
        move = ScanMove(
            axis=Axis.Y,
            direction=(
                Direction.POSITIVE if y_delta > 0 else Direction.NEGATIVE
            ),
            distance_mm=abs(y_delta),
            dwell_after_seconds=0.0,
            acquiring=False,
        )
        return_moves.append(move)
        position = _apply(position, move)

    return ScanRound(
        index=index,
        start=start,
        scan_moves=tuple(scan_moves),
        scan_end=scan_end,
        return_moves=tuple(return_moves),
        end=position,
    )


def build_scan_plan(
    parameters: ScanParameters,
    *,
    start: Position,
) -> ScanPlan:
    _validate_parameters(parameters, start)
    rounds = tuple(
        _build_round(parameters, start, index + 1)
        for index in range(parameters.scan_count)
    )
    return ScanPlan(parameters=parameters, start=start, rounds=rounds)
