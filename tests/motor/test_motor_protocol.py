import pytest

from spectrometer.motor.models import Axis, Direction
from spectrometer.motor.protocol import (
    MotorLineBuffer,
    build_clear_fault_command,
    build_home_command,
    build_id_command,
    build_move_command,
    build_position_set_command,
    build_speed_command,
    build_stop_command,
    parse_response,
)


def test_protocol_v2_command_encoding():
    assert build_id_command() == b"ID?\r\n"
    assert build_speed_command(Axis.X, 10_000) == b"SPEED1=10000\r\n"
    assert (
        build_move_command(Axis.Y, 0.5, Direction.NEGATIVE)
        == b"MOVE2=0.500:0\r\n"
    )
    assert build_position_set_command(Axis.Z, 0.0) == b"POSSET=Z:0.000\r\n"
    assert build_home_command(Axis.X) == b"HOME=X\r\n"
    assert build_home_command(None) == b"HOME=ALL\r\n"
    assert build_stop_command() == b"STOP\r\n"
    assert build_clear_fault_command() == b"CLEARFAULT\r\n"


def test_command_builders_reject_invalid_values_instead_of_clamping():
    with pytest.raises(ValueError, match="speed"):
        build_speed_command(Axis.X, 14_001)
    with pytest.raises(ValueError, match="distance"):
        build_move_command(Axis.X, 0.0, Direction.POSITIVE)
    with pytest.raises(ValueError, match="position"):
        build_position_set_command(Axis.X, -1.0)


def test_line_buffer_handles_split_packets_crlf_and_multiple_lines():
    buffer = MotorLineBuffer()
    assert buffer.feed(b"OK ID=TMC") == ()
    assert buffer.feed(b"2209 MOTOR_PROTOCOL=2\r") == (
        "OK ID=TMC2209 MOTOR_PROTOCOL=2",
    )
    assert buffer.feed(b"\nOK LIMIT X=0 Y=1 Z=0\r\n") == (
        "OK LIMIT X=0 Y=1 Z=0",
    )


def test_structured_response_keeps_unknown_fields_for_forward_compatibility():
    response = parse_response(
        "OK STATUS X_POS=1.250 X_VALID=1 X_MOVING=0 FUTURE=value"
    )
    assert response.ok is True
    assert response.kind == "STATUS"
    assert response.fields["X_POS"] == "1.250"
    assert response.fields["FUTURE"] == "value"

    error = parse_response("ERR TRAVEL_RANGE")
    assert error.ok is False
    assert error.error == "TRAVEL_RANGE"

