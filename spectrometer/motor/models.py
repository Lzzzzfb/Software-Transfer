"""Pure motor domain models shared by the UI and transport layers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math


MOTOR_PULSES_PER_MM = 640
MOTOR_TRAVEL_MM = 15.0
MOTOR_SPEED_MIN_HZ = 100
MOTOR_SPEED_MAX_HZ = 14_000
MOTOR_SPEED_DEFAULT_HZ = 10_000
MINIMUM_MOVE_MM = 1.0 / MOTOR_PULSES_PER_MM


class Axis(str, Enum):
    X = "X"
    Y = "Y"
    Z = "Z"

    @property
    def motor_number(self) -> int:
        return {Axis.X: 1, Axis.Y: 2, Axis.Z: 3}[self]


class Direction(int, Enum):
    NEGATIVE = 0
    POSITIVE = 1

    @property
    def protocol_value(self) -> int:
        return int(self.value)

    @property
    def sign(self) -> int:
        return 1 if self is Direction.POSITIVE else -1


@dataclass(frozen=True)
class Position:
    x_mm: float
    y_mm: float
    z_mm: float = 0.0

    def __post_init__(self):
        for name, value in (
            ("x", self.x_mm),
            ("y", self.y_mm),
            ("z", self.z_mm),
        ):
            if not math.isfinite(value):
                raise ValueError(f"{name} position must be finite")
            if not 0.0 <= value <= MOTOR_TRAVEL_MM:
                raise ValueError(
                    f"{name} position must be between 0 and "
                    f"{MOTOR_TRAVEL_MM:g} mm"
                )

    def for_axis(self, axis: Axis) -> float:
        return {
            Axis.X: self.x_mm,
            Axis.Y: self.y_mm,
            Axis.Z: self.z_mm,
        }[Axis(axis)]


@dataclass(frozen=True)
class MotorConfiguration:
    speed_hz: int = MOTOR_SPEED_DEFAULT_HZ

    def __post_init__(self):
        if isinstance(self.speed_hz, bool) or not isinstance(self.speed_hz, int):
            raise ValueError("speed must be an integer pulse frequency")
        if not MOTOR_SPEED_MIN_HZ <= self.speed_hz <= MOTOR_SPEED_MAX_HZ:
            raise ValueError(
                f"speed must be between {MOTOR_SPEED_MIN_HZ} and "
                f"{MOTOR_SPEED_MAX_HZ} pulse/s"
            )


@dataclass(frozen=True)
class MotorAxisStatus:
    axis: Axis
    position_mm: float | None
    position_valid: bool
    moving: bool
    zero_limit_active: bool
    fault_latched: bool = False
    stop_reason: str = "NONE"
    home_state: str = "IDLE"

    def __post_init__(self):
        object.__setattr__(self, "axis", Axis(self.axis))
        if self.position_valid and self.position_mm is None:
            raise ValueError("position is required when position_valid is true")
        if self.position_mm is not None:
            if not math.isfinite(self.position_mm):
                raise ValueError("position must be finite")
            if not 0.0 <= self.position_mm <= MOTOR_TRAVEL_MM:
                raise ValueError(
                    f"position must be between 0 and {MOTOR_TRAVEL_MM:g} mm"
                )


@dataclass(frozen=True)
class MotorStatus:
    x: MotorAxisStatus
    y: MotorAxisStatus
    z: MotorAxisStatus

    def __post_init__(self):
        if (self.x.axis, self.y.axis, self.z.axis) != (
            Axis.X,
            Axis.Y,
            Axis.Z,
        ):
            raise ValueError("motor status axes must be X, Y, Z")

    @property
    def moving(self) -> bool:
        return self.x.moving or self.y.moving or self.z.moving

    @property
    def fault_latched(self) -> bool:
        return (
            self.x.fault_latched
            or self.y.fault_latched
            or self.z.fault_latched
        )

    @property
    def position(self) -> Position | None:
        if not (
            self.x.position_valid
            and self.y.position_valid
            and self.z.position_valid
        ):
            return None
        return Position(
            self.x.position_mm,
            self.y.position_mm,
            self.z.position_mm,
        )

