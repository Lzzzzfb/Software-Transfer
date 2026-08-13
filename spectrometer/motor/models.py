"""Two-axis motor domain models shared by UI, scan and controller layers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math


MOTOR_PULSES_PER_MM = 320
MOTOR_TRAVEL_MM = 15.0
MOTOR_TRAVEL_PULSES = round(MOTOR_TRAVEL_MM * MOTOR_PULSES_PER_MM)
MOTOR_SPEED_MIN_HZ = 100
MOTOR_SPEED_MAX_HZ = 14_000
MOTOR_SPEED_DEFAULT_HZ = 10_000
MINIMUM_MOVE_MM = 1.0 / MOTOR_PULSES_PER_MM


def _non_zero_speed(speed_pps) -> int:
    speed = abs(int(speed_pps))
    if speed == 0:
        raise ValueError("speed must be non-zero")
    return speed


def stall_release_timeout_seconds(speed_pps) -> float:
    return min(1.0, MOTOR_TRAVEL_PULSES / _non_zero_speed(speed_pps))


def home_timeout_seconds(speed_pps) -> float:
    calculated = MOTOR_TRAVEL_PULSES / _non_zero_speed(speed_pps) * 3.0 + 0.5
    return min(5.0, max(2.0, calculated))


class Axis(str, Enum):
    X = "X"
    Y = "Y"

    @property
    def motor_number(self):
        return 1 if self is Axis.X else 2


class Direction(int, Enum):
    NEGATIVE = 0
    POSITIVE = 1

    @property
    def sign(self):
        return 1 if self is Direction.POSITIVE else -1


@dataclass(frozen=True)
class Position:
    x_mm: float
    y_mm: float

    def __post_init__(self):
        for name, value in (("x", self.x_mm), ("y", self.y_mm)):
            if not math.isfinite(value):
                raise ValueError(f"{name} position must be finite")
            if not 0 <= value <= MOTOR_TRAVEL_MM:
                raise ValueError(f"{name} position must be between 0 and 15 mm")

    def for_axis(self, axis):
        return self.x_mm if Axis(axis) is Axis.X else self.y_mm


@dataclass(frozen=True)
class MotorConfiguration:
    speed_hz: int = MOTOR_SPEED_DEFAULT_HZ

    def __post_init__(self):
        if isinstance(self.speed_hz, bool) or not isinstance(self.speed_hz, int):
            raise ValueError("speed must be an integer pulse frequency")
        if not MOTOR_SPEED_MIN_HZ <= self.speed_hz <= MOTOR_SPEED_MAX_HZ:
            raise ValueError("speed must be between 100 and 14000 pulse/s")


@dataclass(frozen=True)
class MotorAxisStatus:
    axis: Axis
    device_position_pulses: int | None
    software_position_mm: float | None
    calibrated: bool
    moving: bool
    zero_limit_active: bool | None
    stop_reason: str = "NONE"
    mechanical_position_mm: float | None = None

    def __post_init__(self):
        object.__setattr__(self, "axis", Axis(self.axis))
        if self.software_position_mm is not None and not math.isfinite(self.software_position_mm):
            raise ValueError("software position must be finite")
        if self.mechanical_position_mm is not None and not math.isfinite(self.mechanical_position_mm):
            raise ValueError("mechanical position must be finite")
        if self.zero_limit_active is not None and not isinstance(
            self.zero_limit_active, bool
        ):
            raise ValueError("zero limit state must be true, false or unknown")

    @property
    def position_mm(self):
        return self.software_position_mm

    @property
    def position_valid(self):
        return self.calibrated


@dataclass(frozen=True)
class MotorStatus:
    x: MotorAxisStatus
    y: MotorAxisStatus
    fault_latched: bool = False
    fault_reason: str = ""

    def __post_init__(self):
        if (self.x.axis, self.y.axis) != (Axis.X, Axis.Y):
            raise ValueError("motor status axes must be X and Y")

    @property
    def moving(self):
        return self.x.moving or self.y.moving

    @property
    def position(self):
        if (
            not self.x.calibrated
            or not self.y.calibrated
            or self.x.mechanical_position_mm is None
            or self.y.mechanical_position_mm is None
        ):
            return None
        return Position(
            self.x.mechanical_position_mm,
            self.y.mechanical_position_mm,
        )
