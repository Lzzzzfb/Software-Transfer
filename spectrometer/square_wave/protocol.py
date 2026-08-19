"""Deterministic line protocol for the STM32 square-wave generator."""

from __future__ import annotations

import re

from .models import (
    DEVICE_NAME,
    PROTOCOL_VERSION,
    DeviceIdentity,
    DeviceStatus,
    ErrorResponse,
    OkResponse,
    SquareWaveParameters,
)


class ProtocolError(ValueError):
    pass


def _line(value: str) -> bytes:
    try:
        return f"{value}\r\n".encode("ascii")
    except UnicodeEncodeError as exc:  # pragma: no cover - constants are ASCII
        raise ProtocolError("command is not ASCII") from exc


def id_command() -> bytes:
    return _line("ID?")


def status_command() -> bytes:
    return _line("STATUS?")


def pulse_frequency_command(frequency_hz: int) -> bytes:
    parameters = SquareWaveParameters(frequency_hz, 1)
    return _line(f"pulse_freq={parameters.frequency_hz}")


def pulse_width_command(pulse_width_us: int) -> bytes:
    parameters = SquareWaveParameters(1, pulse_width_us)
    return _line(f"pulse_width={parameters.pulse_width_us}")


def start_command() -> bytes:
    return _line("START")


def stop_command() -> bytes:
    return _line("STOP")


_IDENTITY = re.compile(r"^ID ([A-Z0-9_]+) protocol=([0-9]+)$")
_STATUS = re.compile(
    r"^STATUS running=([01]) freq=([0-9]+) width=([0-9]+)$"
)


def parse_response_line(line: str):
    if not isinstance(line, str) or not line:
        raise ProtocolError("empty response")
    if line == "OK":
        return OkResponse()
    if line in {"ERROR", "INVALID PARAM", "UNKNOWN COMMAND"}:
        return ErrorResponse(line)

    match = _IDENTITY.fullmatch(line)
    if match:
        identity = DeviceIdentity(match.group(1), int(match.group(2)))
        if identity.name != DEVICE_NAME:
            raise ProtocolError(f"unexpected device identity: {identity.name}")
        if identity.protocol_version != PROTOCOL_VERSION:
            raise ProtocolError(
                "unsupported square-wave protocol version: "
                f"{identity.protocol_version}"
            )
        return identity

    match = _STATUS.fullmatch(line)
    if match:
        try:
            parameters = SquareWaveParameters(
                int(match.group(2)), int(match.group(3))
            )
        except ValueError as exc:
            raise ProtocolError(str(exc)) from exc
        return DeviceStatus(match.group(1) == "1", parameters)

    raise ProtocolError(f"unrecognized response: {line}")


class ResponseLineBuffer:
    def __init__(self, maximum_line_bytes: int = 256):
        if maximum_line_bytes <= 0:
            raise ValueError("maximum line length must be positive")
        self.maximum_line_bytes = int(maximum_line_bytes)
        self._buffer = bytearray()

    def clear(self) -> None:
        self._buffer.clear()

    def feed(self, data: bytes) -> tuple[str, ...]:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise ProtocolError("serial input must be bytes")
        lines: list[str] = []
        for value in bytes(data):
            if value in (0x0D, 0x0A):
                if not self._buffer:
                    continue
                try:
                    lines.append(self._buffer.decode("ascii"))
                except UnicodeDecodeError as exc:
                    self._buffer.clear()
                    raise ProtocolError("response is not ASCII") from exc
                self._buffer.clear()
                continue
            self._buffer.append(value)
            if len(self._buffer) > self.maximum_line_bytes:
                self._buffer.clear()
                raise ProtocolError("response line exceeds buffer limit")
        return tuple(lines)
