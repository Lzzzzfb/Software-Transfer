import pytest

from spectrometer.square_wave.models import (
    DeviceIdentity,
    DeviceStatus,
    ErrorResponse,
    OkResponse,
    SquareWaveParameters,
)
from spectrometer.square_wave.protocol import (
    ProtocolError,
    ResponseLineBuffer,
    id_command,
    parse_response_line,
    pulse_frequency_command,
    pulse_width_command,
    start_command,
    status_command,
    stop_command,
)


def test_commands_use_exact_ascii_and_crlf():
    assert id_command() == b"ID?\r\n"
    assert status_command() == b"STATUS?\r\n"
    assert pulse_frequency_command(10) == b"pulse_freq=10\r\n"
    assert pulse_width_command(5) == b"pulse_width=5\r\n"
    assert start_command() == b"START\r\n"
    assert stop_command() == b"STOP\r\n"
    assert b"START:" not in start_command()


def test_command_builders_validate_parameters():
    with pytest.raises(ValueError):
        pulse_frequency_command(11)
    with pytest.raises(ValueError):
        pulse_width_command(10000)


def test_identity_status_ok_and_error_responses_parse_strictly():
    assert parse_response_line("ID ZGCAI_SQUARE_WAVE protocol=1") == (
        DeviceIdentity("ZGCAI_SQUARE_WAVE", 1)
    )
    assert parse_response_line("STATUS running=1 freq=10 width=5") == (
        DeviceStatus(True, SquareWaveParameters(10, 5))
    )
    assert parse_response_line("OK") == OkResponse()
    assert parse_response_line("INVALID PARAM") == ErrorResponse(
        "INVALID PARAM"
    )
    assert parse_response_line("ERROR") == ErrorResponse("ERROR")
    assert parse_response_line("UNKNOWN COMMAND") == ErrorResponse(
        "UNKNOWN COMMAND"
    )


@pytest.mark.parametrize(
    "line",
    [
        "",
        "ID OTHER protocol=1",
        "ID ZGCAI_SQUARE_WAVE protocol=2",
        "ID ZGCAI_SQUARE_WAVE protocol=1 extra=1",
        "STATUS running=2 freq=10 width=5",
        "STATUS running=0 freq=11 width=5",
        "STATUS running=0 freq=10 width=10000",
        "STATUS freq=10 running=0 width=5",
        "STATUS running=0 freq=10 freq=9 width=5",
        "OK extra",
        "SOMETHING ELSE",
    ],
)
def test_malformed_or_unconfirmed_responses_are_rejected(line):
    with pytest.raises(ProtocolError):
        parse_response_line(line)


def test_line_buffer_handles_fragments_crlf_and_multiple_lines():
    buffer = ResponseLineBuffer()
    assert buffer.feed(b"ID ZGCAI_") == ()
    assert buffer.feed(b"SQUARE_WAVE protocol=1\r") == (
        "ID ZGCAI_SQUARE_WAVE protocol=1",
    )
    assert buffer.feed(b"\nOK\r\nSTATUS running=0 freq=10") == ("OK",)
    assert buffer.feed(b" width=5\n") == (
        "STATUS running=0 freq=10 width=5",
    )


def test_line_buffer_rejects_non_ascii_and_unbounded_input():
    buffer = ResponseLineBuffer(maximum_line_bytes=8)
    with pytest.raises(ProtocolError):
        buffer.feed(b"123456789")
    buffer = ResponseLineBuffer()
    with pytest.raises(ProtocolError):
        buffer.feed(b"\xff\n")
