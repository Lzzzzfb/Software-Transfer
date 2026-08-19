import pytest

from spectrometer.motor.modbus_rtu import (
    ModbusCrcError,
    ModbusDeviceError,
    ModbusFrameError,
    ModbusResponseBuffer,
    append_crc,
    build_read_holding,
    build_write_multiple,
    build_write_single,
    crc16_modbus,
    int32_to_registers,
    parse_response,
    registers_to_int32,
    registers_to_uint32,
    uint32_to_registers,
    verify_crc,
)


def test_manual_write_multiple_example_matches_exact_bytes():
    request = build_write_multiple(1, 1, (0x000A, 0x0102))
    assert request == bytes.fromhex(
        "01 10 00 01 00 02 04 00 0A 01 02 92 30"
    )
    response = parse_response(
        bytes.fromhex("01 10 00 01 00 02 10 08"),
        expected_address=1,
        expected_function=0x10,
    )
    assert response.data == bytes.fromhex("00 01 00 02")


def test_crc_is_appended_low_byte_then_high_byte():
    body = bytes.fromhex("01 03 00 10 00 01")
    frame = append_crc(body)
    crc = crc16_modbus(body)
    assert frame[-2:] == bytes((crc & 0xFF, crc >> 8))
    assert verify_crc(frame)
    assert not verify_crc(frame[:-1] + bytes((frame[-1] ^ 1,)))


def test_build_read_and_single_write_frames():
    assert build_read_holding(1, 0x10, 2) == append_crc(
        bytes.fromhex("01 03 00 10 00 02")
    )
    assert build_write_single(1, 0x19, 0) == append_crc(
        bytes.fromhex("01 06 00 19 00 00")
    )


def test_parse_read_registers_and_validate_shape():
    frame = append_crc(bytes.fromhex("01 03 04 12 34 AB CD"))
    response = parse_response(
        frame, expected_address=1, expected_function=0x03
    )
    assert response.registers == (0x1234, 0xABCD)
    with pytest.raises(ModbusFrameError, match="address"):
        parse_response(frame, expected_address=2)
    with pytest.raises(ModbusCrcError):
        parse_response(frame[:-1] + bytes((frame[-1] ^ 1,)))


def test_parse_exception_response():
    frame = append_crc(bytes((1, 0x83, 0x02)))
    with pytest.raises(ModbusDeviceError) as caught:
        parse_response(frame, expected_address=1, expected_function=0x03)
    assert caught.value.exception_code == 2


@pytest.mark.parametrize(
    "value,registers",
    [
        (0, (0, 0)),
        (0x12345678, (0x1234, 0x5678)),
        (0xFFFFFFFF, (0xFFFF, 0xFFFF)),
    ],
)
def test_uint32_register_conversion(value, registers):
    assert uint32_to_registers(value) == registers
    assert registers_to_uint32(registers) == value


@pytest.mark.parametrize("value", [-(1 << 31), -4800, -1, 0, 4800, (1 << 31) - 1])
def test_int32_register_conversion(value):
    assert registers_to_int32(int32_to_registers(value)) == value


def test_response_buffer_handles_fragments_noise_and_multiple_frames():
    first = append_crc(bytes.fromhex("01 03 02 00 01"))
    second = append_crc(bytes.fromhex("01 03 02 00 02"))
    buffer = ModbusResponseBuffer()
    assert buffer.feed(
        b"noise" + first[:3], expected_address=1, expected_function=3
    ) == ()
    frames = buffer.feed(
        first[3:] + second, expected_address=1, expected_function=3
    )
    assert frames == (first, second)
    assert buffer.pending == b""


def test_response_buffer_recovers_after_bad_crc():
    bad = bytearray(append_crc(bytes.fromhex("01 03 02 00 01")))
    bad[-1] ^= 1
    good = append_crc(bytes.fromhex("01 03 02 00 02"))
    buffer = ModbusResponseBuffer(max_size=32)
    assert buffer.feed(
        bytes(bad) + good, expected_address=1, expected_function=3
    ) == (good,)


def test_validation_rejects_invalid_ranges():
    with pytest.raises(ValueError):
        build_read_holding(0, 0, 1)
    with pytest.raises(ValueError):
        build_read_holding(1, 0, 126)
    with pytest.raises(ValueError):
        build_write_multiple(1, 0, ())
    with pytest.raises(ValueError):
        int32_to_registers(1 << 31)
