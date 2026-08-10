"""Small, strict Modbus-RTU codec used by the LK-MD2202 adapter."""

from __future__ import annotations

from dataclasses import dataclass


class ModbusError(ValueError):
    """Base class for protocol-level errors."""


class ModbusCrcError(ModbusError):
    pass


class ModbusFrameError(ModbusError):
    pass


class ModbusDeviceError(ModbusError):
    def __init__(self, function: int, exception_code: int):
        self.function = int(function)
        self.exception_code = int(exception_code)
        super().__init__(
            f"Modbus exception 0x{exception_code:02X} for function "
            f"0x{function:02X}"
        )


@dataclass(frozen=True)
class ModbusResponse:
    address: int
    function: int
    data: bytes
    raw: bytes

    @property
    def registers(self) -> tuple[int, ...]:
        if self.function != 0x03:
            raise ModbusFrameError("response does not contain registers")
        if len(self.data) % 2:
            raise ModbusFrameError("register payload has odd byte length")
        return tuple(
            int.from_bytes(self.data[index : index + 2], "big")
            for index in range(0, len(self.data), 2)
        )


def _u8(value: int, name: str, *, minimum: int = 0, maximum: int = 255) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _u16(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not 0 <= value <= 0xFFFF:
        raise ValueError(f"{name} must be between 0 and 65535")
    return value


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for value in bytes(data):
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def append_crc(frame: bytes) -> bytes:
    frame = bytes(frame)
    crc = crc16_modbus(frame)
    return frame + bytes((crc & 0xFF, crc >> 8))


def verify_crc(frame: bytes) -> bool:
    frame = bytes(frame)
    if len(frame) < 4:
        return False
    expected = frame[-2] | frame[-1] << 8
    return crc16_modbus(frame[:-2]) == expected


def build_read_holding(address: int, start: int, count: int) -> bytes:
    address = _u8(address, "address", minimum=1, maximum=247)
    start = _u16(start, "start")
    count = _u16(count, "count")
    if not 1 <= count <= 125:
        raise ValueError("count must be between 1 and 125")
    return append_crc(
        bytes((address, 0x03))
        + start.to_bytes(2, "big")
        + count.to_bytes(2, "big")
    )


def build_write_single(address: int, register: int, value: int) -> bytes:
    address = _u8(address, "address", minimum=1, maximum=247)
    register = _u16(register, "register")
    value = _u16(value, "value")
    return append_crc(
        bytes((address, 0x06))
        + register.to_bytes(2, "big")
        + value.to_bytes(2, "big")
    )


def build_write_multiple(
    address: int,
    start: int,
    registers: tuple[int, ...] | list[int],
) -> bytes:
    address = _u8(address, "address", minimum=1, maximum=247)
    start = _u16(start, "start")
    values = tuple(_u16(value, "register value") for value in registers)
    if not 1 <= len(values) <= 123:
        raise ValueError("register count must be between 1 and 123")
    payload = b"".join(value.to_bytes(2, "big") for value in values)
    return append_crc(
        bytes((address, 0x10))
        + start.to_bytes(2, "big")
        + len(values).to_bytes(2, "big")
        + bytes((len(payload),))
        + payload
    )


def uint32_to_registers(value: int) -> tuple[int, int]:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("value must be an integer")
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError("uint32 value out of range")
    return value >> 16, value & 0xFFFF


def int32_to_registers(value: int) -> tuple[int, int]:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("value must be an integer")
    if not -(1 << 31) <= value <= (1 << 31) - 1:
        raise ValueError("int32 value out of range")
    return uint32_to_registers(value & 0xFFFFFFFF)


def registers_to_uint32(registers) -> int:
    values = tuple(registers)
    if len(values) != 2:
        raise ValueError("exactly two registers are required")
    high, low = (_u16(value, "register") for value in values)
    return high << 16 | low


def registers_to_int32(registers) -> int:
    value = registers_to_uint32(registers)
    return value - (1 << 32) if value & 0x80000000 else value


def parse_response(
    frame: bytes,
    *,
    expected_address: int | None = None,
    expected_function: int | None = None,
) -> ModbusResponse:
    frame = bytes(frame)
    if len(frame) < 5:
        raise ModbusFrameError("response is too short")
    if not verify_crc(frame):
        raise ModbusCrcError("response CRC mismatch")
    address, function = frame[0], frame[1]
    if expected_address is not None and address != expected_address:
        raise ModbusFrameError("response address mismatch")
    if function & 0x80:
        base_function = function & 0x7F
        if len(frame) != 5:
            raise ModbusFrameError("invalid exception response length")
        if expected_function is not None and base_function != expected_function:
            raise ModbusFrameError("exception function mismatch")
        raise ModbusDeviceError(base_function, frame[2])
    if expected_function is not None and function != expected_function:
        raise ModbusFrameError("response function mismatch")
    if function == 0x03:
        byte_count = frame[2]
        if len(frame) != byte_count + 5 or byte_count % 2:
            raise ModbusFrameError("invalid read response length")
        data = frame[3:-2]
    elif function in (0x06, 0x10):
        if len(frame) != 8:
            raise ModbusFrameError("invalid write response length")
        data = frame[2:-2]
    else:
        raise ModbusFrameError(f"unsupported response function 0x{function:02X}")
    return ModbusResponse(address, function, data, frame)


class ModbusResponseBuffer:
    """Extract responses for the single transaction currently in flight."""

    def __init__(self, max_size: int = 4096):
        if max_size < 8:
            raise ValueError("max_size must be at least 8")
        self.max_size = int(max_size)
        self._buffer = bytearray()

    def clear(self):
        self._buffer.clear()

    @property
    def pending(self) -> bytes:
        return bytes(self._buffer)

    def feed(
        self,
        data: bytes,
        *,
        expected_address: int,
        expected_function: int,
    ) -> tuple[bytes, ...]:
        self._buffer.extend(bytes(data))
        if len(self._buffer) > self.max_size:
            del self._buffer[: len(self._buffer) - self.max_size]
        frames: list[bytes] = []
        normal = expected_function
        exception = expected_function | 0x80
        while len(self._buffer) >= 2:
            start = next(
                (
                    index
                    for index in range(len(self._buffer) - 1)
                    if self._buffer[index] == expected_address
                    and self._buffer[index + 1] in (normal, exception)
                ),
                None,
            )
            if start is None:
                if self._buffer[-1] == expected_address:
                    del self._buffer[:-1]
                else:
                    self._buffer.clear()
                break
            if start:
                del self._buffer[:start]
            function = self._buffer[1]
            if function == exception:
                length = 5
            elif function in (0x06, 0x10):
                length = 8
            elif function == 0x03:
                if len(self._buffer) < 3:
                    break
                length = self._buffer[2] + 5
                if length > self.max_size:
                    del self._buffer[0]
                    continue
            else:  # pragma: no cover - filtered above
                del self._buffer[0]
                continue
            if len(self._buffer) < length:
                break
            candidate = bytes(self._buffer[:length])
            if not verify_crc(candidate):
                del self._buffer[0]
                continue
            frames.append(candidate)
            del self._buffer[:length]
        return tuple(frames)

