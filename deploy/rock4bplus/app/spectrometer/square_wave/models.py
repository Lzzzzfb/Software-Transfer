"""Pure domain models for the STM32 square-wave generator."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


DEVICE_NAME = "ZGCAI_SQUARE_WAVE"
PROTOCOL_VERSION = 1
MIN_FREQUENCY_HZ = 1
MAX_FREQUENCY_HZ = 10
DEFAULT_FREQUENCY_HZ = 10
MIN_PULSE_WIDTH_US = 1
MAX_PULSE_WIDTH_US = 9999
DEFAULT_PULSE_WIDTH_US = 5
DEFAULT_BAUD_RATE = 9600


def _strict_integer(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


@dataclass(frozen=True)
class SquareWaveParameters:
    frequency_hz: int = DEFAULT_FREQUENCY_HZ
    pulse_width_us: int = DEFAULT_PULSE_WIDTH_US

    def __post_init__(self):
        frequency = _strict_integer(self.frequency_hz, "frequency")
        pulse_width = _strict_integer(self.pulse_width_us, "pulse width")
        if not MIN_FREQUENCY_HZ <= frequency <= MAX_FREQUENCY_HZ:
            raise ValueError("frequency must be between 1 and 10 Hz")
        if not MIN_PULSE_WIDTH_US <= pulse_width <= MAX_PULSE_WIDTH_US:
            raise ValueError("pulse width must be between 1 and 9999 us")
        if pulse_width >= 1_000_000 / frequency:
            raise ValueError("pulse width must be shorter than one period")


class OutputOwner(str, Enum):
    NONE = "none"
    MANUAL = "manual"
    SCAN = "scan"


class OutputState(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DeviceIdentity:
    name: str
    protocol_version: int

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("device name must be non-empty")
        _strict_integer(self.protocol_version, "protocol version")

    @property
    def supported(self) -> bool:
        return (
            self.name == DEVICE_NAME
            and self.protocol_version == PROTOCOL_VERSION
        )


@dataclass(frozen=True)
class DeviceStatus:
    running: bool
    parameters: SquareWaveParameters

    def __post_init__(self):
        if not isinstance(self.running, bool):
            raise ValueError("running state must be boolean")
        if not isinstance(self.parameters, SquareWaveParameters):
            raise ValueError("invalid square-wave parameters")

    @property
    def output_state(self) -> OutputState:
        return OutputState.RUNNING if self.running else OutputState.STOPPED


@dataclass(frozen=True)
class OkResponse:
    """The device accepted a command."""


@dataclass(frozen=True)
class ErrorResponse:
    message: str

    def __post_init__(self):
        if self.message not in {
            "ERROR",
            "INVALID PARAM",
            "UNKNOWN COMMAND",
        }:
            raise ValueError("unknown device error response")


@dataclass(frozen=True)
class PortCandidate:
    port_name: str
    system_location: str
    serial_number: str = ""
    description: str = ""
    manufacturer: str = ""
    vendor_id: int | None = None
    product_id: int | None = None

    def __post_init__(self):
        if not isinstance(self.port_name, str) or not self.port_name:
            raise ValueError("port name must be non-empty")
        if not isinstance(self.system_location, str):
            raise ValueError("system location must be text")
