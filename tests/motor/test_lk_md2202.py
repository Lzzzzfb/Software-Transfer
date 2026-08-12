import pytest

from spectrometer.motor.lk_md2202 import (
    AxisConfiguration,
    CommunicationConfiguration,
    DriverAxis,
    END_POSITION_PULSES,
    LEGACY_IDENTITY_REGISTERS,
    LimitMode,
    MINIMUM_MOVE_MM,
    PULSES_PER_MM,
    decode_axis_configuration,
    decode_axis_status,
    decode_communication,
    decode_identity,
    decode_supported_identity,
    home_request,
    identity_request,
    relative_move_request,
    stop_request,
)
from spectrometer.motor.modbus_rtu import append_crc


def name_registers(name):
    raw = name.encode("ascii").ljust(20, b"\0")
    return [int.from_bytes(raw[index:index + 2], "big") for index in range(0, 20, 2)]


def test_identity_uses_version_and_device_name_registers():
    registers = [0x0200, 0, 0, 0, 0, 0] + name_registers("LK-MD2202")
    identity = decode_supported_identity(registers)
    assert identity.software_version == 0x0200
    assert identity.name == "LK-MD2202"
    assert identity.is_lk_md2202
    assert identity_request(1)[0:6] == bytes.fromhex("01 03 00 00 00 10")


def test_exact_legacy_identity_is_supported_without_forging_device_name():
    decoded = decode_identity(LEGACY_IDENTITY_REGISTERS)
    identity = decode_supported_identity(LEGACY_IDENTITY_REGISTERS)
    assert identity == decoded
    assert identity.software_version == 0x0064
    assert identity.name == ""
    assert not identity.is_lk_md2202


def test_legacy_identity_requires_the_complete_exact_signature():
    near_match = list(LEGACY_IDENTITY_REGISTERS)
    near_match[1] ^= 1
    with pytest.raises(ValueError, match="unsupported LK-MD2202 identity"):
        decode_supported_identity(near_match)

    with pytest.raises(ValueError, match="unsupported LK-MD2202 identity"):
        decode_supported_identity([0] * 16)

    with pytest.raises(ValueError):
        decode_supported_identity([0xFFFF] * 16)

    with pytest.raises(ValueError, match="0x0000..0x000F"):
        decode_supported_identity(LEGACY_IDENTITY_REGISTERS[:-1])


def test_confirmed_axis_defaults_match_user_configuration():
    config = AxisConfiguration()
    assert config.microstep.divisor == 8
    assert int(config.run_current) == 4
    assert config.limit_mode is LimitMode.ZERO_POINT
    assert config.end_position_pulses == END_POSITION_PULSES == 4800
    assert config.position_speed_pps == 10_000
    assert config.matches_confirmed_mechanics
    assert PULSES_PER_MM == 320
    assert MINIMUM_MOVE_MM == pytest.approx(0.003125)


def test_decode_axis_configuration_reads_sparse_fields():
    registers = [0] * 16
    registers[0:6] = [0, 3, 4, 1, 0, 4800]
    registers[9:12] = [0, 10000, 10000]
    registers[14:16] = [0, 10000]
    assert decode_axis_configuration(registers) == AxisConfiguration()


def test_status_and_action_registers_are_mapped_per_axis():
    status = decode_axis_status((1, 2, 0xFFFF, 0xED40))
    assert status.limit_active
    assert status.moving
    assert status.position_pulses == -4800
    assert relative_move_request(1, DriverAxis.X, 320)[:6] == bytes.fromhex("01 10 00 20 00 02")
    assert relative_move_request(1, DriverAxis.Y, -320)[:6] == bytes.fromhex("01 10 00 40 00 02")
    assert stop_request(1, DriverAxis.X) == append_crc(bytes.fromhex("01 06 00 24 00 01"))
    assert home_request(1, DriverAxis.Y) == append_crc(bytes.fromhex("01 06 00 45 00 01"))


def test_rejects_z_and_non_zero_limit_mode():
    with pytest.raises(ValueError, match="only X"):
        relative_move_request(1, "Z", 1)
    with pytest.raises(ValueError, match="zero-point"):
        AxisConfiguration(limit_mode=LimitMode.NONE)


def test_communication_requires_confirmed_8n1():
    assert CommunicationConfiguration().baud_rate == 9600
    with pytest.raises(ValueError, match="8N1"):
        CommunicationConfiguration(parity=1)
    with pytest.raises(ValueError, match="8N1"):
        CommunicationConfiguration(stop_bits=1.5)


def test_decode_communication_maps_driver_codes_to_8n1_semantics():
    assert decode_communication((1, 3, 0, 0, 0)) == CommunicationConfiguration()


@pytest.mark.parametrize(
    ("registers", "message"),
    [
        ((1, 3, 1, 0, 0), "8N1"),
        ((1, 3, 0, 1, 0), "8N1"),
        ((1, 3, 0, 2, 0), "8N1"),
        ((1, 3, 0, 0, 1), "8N1"),
        ((1, 3, 0, 0, 2), "8N1"),
        ((1, 8, 0, 0, 0), "baud-rate"),
        ((1, 3, 2, 0, 0), "data-bit"),
        ((1, 3, 0, 3, 0), "stop-bit"),
        ((1, 3, 0, 0, 3), "parity"),
    ],
)
def test_decode_communication_rejects_non_8n1_and_unknown_codes(registers, message):
    with pytest.raises(ValueError, match=message):
        decode_communication(registers)
