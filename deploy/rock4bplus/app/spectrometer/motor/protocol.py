"""Version 2 line protocol used by the STM32 motor controller."""

from __future__ import annotations

from dataclasses import dataclass, field
import math

from .models import (
    MINIMUM_MOVE_MM,
    MOTOR_SPEED_MAX_HZ,
    MOTOR_SPEED_MIN_HZ,
    MOTOR_TRAVEL_MM,
    Axis,
    Direction,
)


PROTOCOL_VERSION = 2
RESPONSE_LINE_LIMIT = 384


@dataclass(frozen=True)
class MotorResponse:
    ok: bool
    kind: str
    fields: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    raw: str = ""


class MotorProtocolError(ValueError):
    pass


class MotorLineBuffer:
    """Incrementally splits CR, LF, or CRLF-delimited UTF-8/ASCII lines."""

    def __init__(self, max_line_size: int = RESPONSE_LINE_LIMIT):
        if max_line_size < 2:
            raise ValueError("max_line_size must be at least 2")
        self.max_line_size = int(max_line_size)
        self._buffer = bytearray()
        self._discarding = False

    def reset(self):
        self._buffer.clear()
        self._discarding = False

    def feed(self, data: bytes) -> tuple[str, ...]:
        lines: list[str] = []
        for value in bytes(data):
            if value in (10, 13):
                if self._discarding:
                    self._discarding = False
                    self._buffer.clear()
                elif self._buffer:
                    try:
                        lines.append(self._buffer.decode("ascii"))
                    except UnicodeDecodeError as exc:
                        self._buffer.clear()
                        raise MotorProtocolError(
                            "motor response is not ASCII"
                        ) from exc
                    self._buffer.clear()
                continue
            if self._discarding:
                continue
            if len(self._buffer) >= self.max_line_size - 1:
                self._buffer.clear()
                self._discarding = True
                continue
            self._buffer.append(value)
        return tuple(lines)


def _line(command: str) -> bytes:
    return f"{command}\r\n".encode("ascii")


def build_id_command() -> bytes:
    return _line("ID?")


def build_status_command() -> bytes:
    return _line("STATUS")


def build_limit_command() -> bytes:
    return _line("LIMIT?")


def build_position_command() -> bytes:
    return _line("POS?")


def build_fault_command() -> bytes:
    return _line("FAULT?")


def build_clear_fault_command() -> bytes:
    return _line("CLEARFAULT")


def build_stop_command() -> bytes:
    return _line("STOP")


def build_enable_command(enabled: bool) -> bytes:
    return _line(f"PB14={0 if enabled else 1}")


def build_speed_command(axis: Axis, speed_hz: int) -> bytes:
    axis = Axis(axis)
    if isinstance(speed_hz, bool) or not isinstance(speed_hz, int):
        raise ValueError("speed must be an integer")
    if not MOTOR_SPEED_MIN_HZ <= speed_hz <= MOTOR_SPEED_MAX_HZ:
        raise ValueError(
            f"speed must be between {MOTOR_SPEED_MIN_HZ} and "
            f"{MOTOR_SPEED_MAX_HZ}"
        )
    return _line(f"SPEED{axis.motor_number}={speed_hz}")


def build_move_command(
    axis: Axis,
    distance_mm: float,
    direction: Direction,
) -> bytes:
    axis = Axis(axis)
    direction = Direction(direction)
    if (
        not math.isfinite(distance_mm)
        or distance_mm < MINIMUM_MOVE_MM
        or distance_mm > MOTOR_TRAVEL_MM
    ):
        raise ValueError(
            f"distance must be between {MINIMUM_MOVE_MM:g} and "
            f"{MOTOR_TRAVEL_MM:g} mm"
        )
    return _line(
        f"MOVE{axis.motor_number}={distance_mm:.3f}:"
        f"{direction.protocol_value}"
    )


def build_position_set_command(axis: Axis, position_mm: float) -> bytes:
    axis = Axis(axis)
    if not math.isfinite(position_mm) or not 0.0 <= position_mm <= MOTOR_TRAVEL_MM:
        raise ValueError(
            f"position must be between 0 and {MOTOR_TRAVEL_MM:g} mm"
        )
    return _line(f"POSSET={axis.value}:{position_mm:.3f}")


def build_home_command(axis: Axis | None) -> bytes:
    target = "ALL" if axis is None else Axis(axis).value
    return _line(f"HOME={target}")


def parse_response(line: str) -> MotorResponse:
    raw = str(line).strip()
    if not raw:
        raise MotorProtocolError("empty motor response")
    tokens = raw.split()
    if tokens[0] == "ERR":
        error = tokens[1] if len(tokens) > 1 else "UNKNOWN"
        return MotorResponse(
            ok=False,
            kind="ERROR",
            error=error,
            raw=raw,
        )
    if tokens[0] != "OK":
        raise MotorProtocolError(f"unknown motor response prefix: {tokens[0]}")

    kind = "ACK"
    fields: dict[str, str] = {}
    for token in tokens[1:]:
        if "=" in token:
            key, value = token.split("=", 1)
            if key:
                fields[key] = value
        elif kind == "ACK":
            kind = token
    if kind == "ACK" and "ID" in fields:
        kind = "ID"
    return MotorResponse(ok=True, kind=kind, fields=fields, raw=raw)

