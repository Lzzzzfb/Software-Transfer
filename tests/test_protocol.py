import pytest

from spectrometer.communication.protocol import (
    CmdCode,
    build_packet,
    parse_data_packet,
    parse_data_packet_view,
    parse_packet,
)


def make_data_frame(
    packet_number=0x01020304,
    pixels=(0x1234, 0xABCD),
    checksum=0x00,
):
    pixel_bytes = b"".join(value.to_bytes(2, "big") for value in pixels)
    length = 6 + len(pixel_bytes)
    return (
        b"\x24"
        + length.to_bytes(2, "little")
        + bytes([CmdCode.DATA_TRANSMIT])
        + packet_number.to_bytes(4, "little")
        + pixel_bytes
        + bytes([checksum])
    )


def test_build_query_packet_uses_ordinary_length_and_checksum():
    assert build_packet(CmdCode.GET_VERSION) == bytes.fromhex("24 01 00 01 02")


def test_parse_ordinary_response_validates_checksum():
    frame = build_packet(0x61, b"\x01\x02")
    assert parse_packet(frame) == (0x61, b"\x01\x02")

    with pytest.raises(ValueError, match="校验码错误"):
        parse_packet(frame[:-1] + b"\x00")


def test_parse_data_frame_uses_u32le_packet_number_and_u16be_pixels():
    frame = make_data_frame()

    assert len(frame) == 13
    assert frame[1:3] == b"\x0A\x00"
    parsed = parse_data_packet(frame, n_pixel=2)
    assert parsed == {
        "packet_number": 0x01020304,
        "frame_sequence": 0x010203,
        "reserved": 0x04,
        "packet_number_bits": 32,
        "protocol_variant": "u32",
        "pixels": [0x1234, 0xABCD],
        "pixel_count": 2,
        "source_pixel_count": 2,
        "checksum": 0x00,
    }


@pytest.mark.parametrize("pixel_count", [0, 1, 2048, 4096])
def test_data_frame_pixel_boundaries(pixel_count):
    pixels = tuple(index & 0xFFFF for index in range(pixel_count))
    frame = make_data_frame(pixels=pixels)
    parsed = parse_data_packet(frame, n_pixel=pixel_count)
    assert len(frame) == 9 + pixel_count * 2
    assert int.from_bytes(frame[1:3], "little") == 6 + pixel_count * 2
    assert parsed["pixels"] == list(pixels)


def test_trigger_mode_values_match_function_table():
    from spectrometer.communication.protocol import TriggerMode

    assert [mode.value for mode in TriggerMode] == [0, 1, 2]


def test_data_checksum_is_consumed_but_not_validated():
    frame = make_data_frame(checksum=0xA5)
    cmd, params = parse_packet(frame)

    assert cmd == CmdCode.DATA_TRANSMIT
    assert params == frame[4:-1]
    assert parse_data_packet(frame)["checksum"] == 0xA5


def test_data_frame_crops_after_decoding_full_pixel_area():
    frame = make_data_frame(pixels=(10, 20, 30, 40))
    parsed = parse_data_packet(
        frame, n_pixel=4, n_start_pixel=1, n_valid_pixel=2
    )

    assert parsed["pixels"] == [20, 30]
    assert parsed["source_pixel_count"] == 4


def test_data_frame_rejects_invalid_length_and_pixel_count():
    invalid_length = bytes.fromhex("24 04 00 80 00 00 00")
    with pytest.raises(ValueError, match="长度非法"):
        parse_data_packet(invalid_length)

    with pytest.raises(ValueError, match="设备信息不一致"):
        parse_data_packet(make_data_frame(), n_pixel=3)


def test_parser_rejects_truncated_or_trailing_frame():
    frame = make_data_frame()
    with pytest.raises(ValueError, match="帧长度不匹配"):
        parse_data_packet(frame[:-1])
    with pytest.raises(ValueError, match="帧长度不匹配"):
        parse_data_packet(frame + b"\x00")


def test_parse_real_firmware_legacy_u16_data_frame():
    pixels = (0x0CF8, 0x0CED, 0x0F7B)
    pixel_bytes = b"".join(value.to_bytes(2, "big") for value in pixels)
    length = 3 + len(pixel_bytes)
    frame = (
        b"\x24" + length.to_bytes(2, "little") + b"\x80"
        + (1).to_bytes(2, "little") + pixel_bytes + b"\x59"
    )
    parsed = parse_data_packet(frame, n_pixel=3)
    assert len(frame) == 4 + length
    assert parsed["packet_number"] == 1
    assert parsed["frame_sequence"] == 1
    assert parsed["reserved"] == 0
    assert parsed["packet_number_bits"] == 16
    assert parsed["protocol_variant"] == "legacy_u16"
    assert parsed["pixels"] == list(pixels)


def test_real_3694_pixel_frame_matches_verified_u16_wire_format():
    pixels = bytes(3694 * 2)
    length = 3 + 3694 * 2
    frame = (
        b"\x24"
        + length.to_bytes(2, "little")
        + b"\x80"
        + b"\x01\x00"
        + pixels
        + b"\xA5"
    )

    parsed = parse_data_packet_view(
        frame, n_pixel=3694, n_start_pixel=32, n_valid_pixel=3648
    )

    assert frame[:4] == bytes.fromhex("24 DF 1C 80")
    assert len(frame) == 7395
    assert parsed["protocol_variant"] == "legacy_u16"
    assert parsed["frame_sequence"] == 1
    assert parsed["source_pixel_count"] == 3694
    assert parsed["pixels"].shape == (3648,)
    assert parsed["raw_pixels"].shape == (3694,)
    assert parsed["pixels"].dtype.str == ">u2"


def test_numpy_view_shares_data_until_explicit_copy():
    frame = make_data_frame(pixels=(10, 20, 30))

    parsed = parse_data_packet_view(
        frame, n_pixel=3, n_start_pixel=1, n_valid_pixel=2
    )

    assert parsed["pixels"].tolist() == [20, 30]
    assert parsed["pixels"].flags["OWNDATA"] is False
