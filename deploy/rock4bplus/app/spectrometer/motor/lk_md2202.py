"""LK-MD2202 register map and typed device semantics."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import IntEnum

from .modbus_rtu import (
    build_read_holding,
    build_write_multiple,
    build_write_single,
    int32_to_registers,
    registers_to_int32,
    registers_to_uint32,
    uint32_to_registers,
)


DEFAULT_ADDRESS = 1
DEFAULT_BAUD_RATE = 9600
TRAVEL_MM = 15.0
END_POSITION_PULSES = 4800
PULSES_PER_MM = 320
MINIMUM_MOVE_MM = 1.0 / PULSES_PER_MM
DEVICE_NAME_START = 0x0006
DEVICE_NAME_COUNT = 10
COMMUNICATION_START = 0x0050


class DriverAxis(IntEnum):
    X = 1
    Y = 2

    @property
    def base(self) -> int:
        return 0x0010 if self is DriverAxis.X else 0x0030


class StepAngle(IntEnum):
    DEG_1_8 = 0
    DEG_0_9 = 1


class Microstep(IntEnum):
    FULL = 0
    X2 = 1
    X4 = 2
    X8 = 3
    X16 = 4
    X32 = 5

    @property
    def divisor(self) -> int:
        return (1, 2, 4, 8, 16, 32)[int(self)]


class RunCurrent(IntEnum):
    P100 = 0
    P87_5 = 1
    P75 = 2
    P62_5 = 3
    P50 = 4
    P37_5 = 5
    P25 = 6
    P12_5 = 7
    OFF = 8


class LimitMode(IntEnum):
    NONE = 0
    ZERO_POINT = 1
    STALL = 2


class StopCurrent(IntEnum):
    UNLOCKED = 0
    HOLDING = 1


BAUD_CODE_TO_RATE = {
    0: 1200,
    1: 2400,
    2: 4800,
    3: 9600,
    4: 19200,
    5: 38400,
    6: 57600,
    7: 115200,
}
BAUD_RATE_TO_CODE = {rate: code for code, rate in BAUD_CODE_TO_RATE.items()}


@dataclass(frozen=True)
class DeviceIdentity:
    software_version: int
    name: str

    @property
    def is_lk_md2202(self) -> bool:
        normalized = self.name.upper().replace("_", "-").replace(" ", "")
        return normalized.startswith("LK-MD2202")


@dataclass(frozen=True)
class AxisConfiguration:
    step_angle: StepAngle = StepAngle.DEG_1_8
    microstep: Microstep = Microstep.X8
    run_current: RunCurrent = RunCurrent.P50
    limit_mode: LimitMode = LimitMode.ZERO_POINT
    end_position_pulses: int = END_POSITION_PULSES
    stop_current: StopCurrent = StopCurrent.UNLOCKED
    acceleration: int = 10_000
    deceleration: int = 10_000
    position_speed_pps: int = 10_000

    def __post_init__(self):
        object.__setattr__(self, "step_angle", StepAngle(self.step_angle))
        object.__setattr__(self, "microstep", Microstep(self.microstep))
        object.__setattr__(self, "run_current", RunCurrent(self.run_current))
        object.__setattr__(self, "limit_mode", LimitMode(self.limit_mode))
        object.__setattr__(self, "stop_current", StopCurrent(self.stop_current))
        if self.limit_mode is not LimitMode.ZERO_POINT:
            raise ValueError("LK-MD2202 must use zero-point limit mode")
        if not 1 <= int(self.end_position_pulses) <= 0xFFFFFFFF:
            raise ValueError("end position pulses out of range")
        for name in ("acceleration", "deceleration"):
            value = int(getattr(self, name))
            if not 1 <= value <= 0xFFFF:
                raise ValueError(f"{name} out of range")
        if not 1 <= int(self.position_speed_pps) <= 0xFFFFFFFF:
            raise ValueError("position speed out of range")

    @property
    def pulses_per_mm(self) -> float:
        return self.end_position_pulses / TRAVEL_MM

    @property
    def matches_confirmed_mechanics(self) -> bool:
        return (
            self.step_angle is StepAngle.DEG_1_8
            and self.microstep is Microstep.X8
            and self.end_position_pulses == END_POSITION_PULSES
            and self.pulses_per_mm == PULSES_PER_MM
        )


@dataclass(frozen=True)
class CommunicationConfiguration:
    address: int = DEFAULT_ADDRESS
    baud_rate: int = DEFAULT_BAUD_RATE
    data_bits: int = 8
    stop_bits: int = 1
    parity: int = 0

    def __post_init__(self):
        if not 1 <= int(self.address) <= 247:
            raise ValueError("Modbus address must be between 1 and 247")
        if int(self.baud_rate) not in BAUD_RATE_TO_CODE:
            raise ValueError("unsupported baud rate")
        if (int(self.data_bits), int(self.stop_bits), int(self.parity)) != (8, 1, 0):
            raise ValueError("this application requires 8N1 without parity")


@dataclass(frozen=True)
class DeviceConfiguration:
    x: AxisConfiguration = AxisConfiguration()
    y: AxisConfiguration = AxisConfiguration()
    communication: CommunicationConfiguration = CommunicationConfiguration()


@dataclass(frozen=True)
class AxisDeviceStatus:
    limit_active: bool
    run_state: int
    position_pulses: int

    @property
    def moving(self) -> bool:
        return self.run_state in (1, 2)


def _axis(axis) -> DriverAxis:
    if isinstance(axis, DriverAxis):
        return axis
    value = getattr(axis, "value", axis)
    if str(value).upper() == "X":
        return DriverAxis.X
    if str(value).upper() == "Y":
        return DriverAxis.Y
    raise ValueError("LK-MD2202 supports only X/M1 and Y/M2")


def decode_device_name(registers) -> str:
    raw = b"".join(int(value).to_bytes(2, "big") for value in registers)
    return raw.split(b"\0", 1)[0].decode("ascii", errors="strict").strip()


def decode_identity(registers) -> DeviceIdentity:
    values = tuple(registers)
    if len(values) < 16:
        raise ValueError("identity response requires registers 0x0000..0x000F")
    return DeviceIdentity(values[0], decode_device_name(values[6:16]))


def identity_request(address: int) -> bytes:
    return build_read_holding(address, 0x0000, 16)


def axis_configuration_request(address: int, axis) -> bytes:
    return build_read_holding(address, _axis(axis).base, 16)


def decode_axis_configuration(registers) -> AxisConfiguration:
    values = tuple(registers)
    if len(values) < 16:
        raise ValueError("axis configuration requires 16 registers")
    return AxisConfiguration(
        step_angle=values[0],
        microstep=values[1],
        run_current=values[2],
        limit_mode=values[3],
        end_position_pulses=registers_to_uint32(values[4:6]),
        stop_current=values[9],
        acceleration=values[10],
        deceleration=values[11],
        position_speed_pps=registers_to_uint32(values[14:16]),
    )


def communication_request(address: int) -> bytes:
    return build_read_holding(address, COMMUNICATION_START, 5)


def decode_communication(registers) -> CommunicationConfiguration:
    values = tuple(registers)
    if len(values) < 5:
        raise ValueError("communication configuration requires five registers")
    try:
        baud_rate = BAUD_CODE_TO_RATE[values[1]]
    except KeyError as exc:
        raise ValueError("unknown baud-rate code") from exc
    return CommunicationConfiguration(
        address=values[0],
        baud_rate=baud_rate,
        data_bits=values[2],
        stop_bits=values[3],
        parity=values[4],
    )


def status_request(address: int, axis) -> bytes:
    return build_read_holding(address, _axis(axis).base + 0x16, 4)


def decode_axis_status(registers) -> AxisDeviceStatus:
    values = tuple(registers)
    if len(values) != 4:
        raise ValueError("axis status requires four registers")
    if values[1] not in (0, 1, 2):
        raise ValueError("invalid motor run state")
    return AxisDeviceStatus(bool(values[0]), values[1], registers_to_int32(values[2:4]))


def relative_move_request(address: int, axis, pulses: int) -> bytes:
    channel = _axis(axis)
    return build_write_multiple(address, channel.base + 0x10, int32_to_registers(pulses))


def position_speed_request(address: int, axis, speed_pps: int) -> bytes:
    channel = _axis(axis)
    return build_write_multiple(address, channel.base + 0x0E, uint32_to_registers(speed_pps))


def stop_request(address: int, axis) -> bytes:
    return build_write_single(address, _axis(axis).base + 0x14, 1)


def home_request(address: int, axis) -> bytes:
    return build_write_single(address, _axis(axis).base + 0x15, 1)


def configuration_changes(
    address: int,
    axis,
    current: AxisConfiguration,
    desired: AxisConfiguration,
) -> tuple[tuple[str, bytes], ...]:
    channel = _axis(axis)
    base = channel.base
    changes: list[tuple[str, bytes]] = []
    scalar = (
        ("step_angle", 0x00, int(desired.step_angle), current.step_angle),
        ("microstep", 0x01, int(desired.microstep), current.microstep),
        ("run_current", 0x02, int(desired.run_current), current.run_current),
        ("limit_mode", 0x03, int(desired.limit_mode), current.limit_mode),
        ("stop_current", 0x09, int(desired.stop_current), current.stop_current),
        ("acceleration", 0x0A, desired.acceleration, current.acceleration),
        ("deceleration", 0x0B, desired.deceleration, current.deceleration),
    )
    for name, offset, value, old in scalar:
        if int(old) != int(value):
            changes.append((name, build_write_single(address, base + offset, int(value))))
    if current.end_position_pulses != desired.end_position_pulses:
        changes.append((
            "end_position_pulses",
            build_write_multiple(address, base + 0x04, uint32_to_registers(desired.end_position_pulses)),
        ))
    if current.position_speed_pps != desired.position_speed_pps:
        changes.append((
            "position_speed_pps",
            build_write_multiple(address, base + 0x0E, uint32_to_registers(desired.position_speed_pps)),
        ))
    return tuple(changes)


def with_position_speed(config: AxisConfiguration, speed_pps: int) -> AxisConfiguration:
    return replace(config, position_speed_pps=int(speed_pps))
