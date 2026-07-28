from spectrometer.communication.frame_decoder import FrameDecoder
from spectrometer.communication.protocol import CmdCode, build_packet

from .test_protocol import make_data_frame


def test_decoder_waits_for_fragmented_data_frame():
    decoder = FrameDecoder()
    frame = make_data_frame()

    assert decoder.feed(frame[:5]) == []
    assert decoder.feed(frame[5:11]) == []
    assert decoder.feed(frame[11:]) == [frame]
    assert decoder.buffer == b""


def test_decoder_splits_concatenated_ordinary_and_data_frames():
    decoder = FrameDecoder()
    ordinary = build_packet(CmdCode.GET_VERSION)
    data = make_data_frame()

    assert decoder.feed(ordinary + data + ordinary) == [ordinary, data, ordinary]


def test_decoder_discards_noise_before_header():
    decoder = FrameDecoder()
    frame = make_data_frame()

    assert decoder.feed(b"\x00\x11\x22" + frame) == [frame]
    assert decoder.discarded_bytes == 3


def test_decoder_skips_invalid_candidate_header_and_resynchronizes():
    decoder = FrameDecoder()
    frame = build_packet(CmdCode.GET_VERSION)
    invalid = bytes.fromhex("24 00 00 10")

    assert decoder.feed(invalid + frame) == [frame]
    assert decoder.invalid_headers >= 1


def test_decoder_recovers_valid_ack_after_bad_ordinary_checksum():
    decoder = FrameDecoder()
    damaged = build_packet(CmdCode.SET_TRIG_MODE, b"\x60")
    damaged = damaged[:-1] + bytes([damaged[-1] ^ 0xFF])
    ack = build_packet(CmdCode.STOP_ACQUISITION, b"\x60")

    assert decoder.feed(damaged + ack) == [ack]
    assert decoder.checksum_failures == 1
    assert decoder.resync_count == 1


def test_decoder_does_not_wait_for_implausibly_large_ordinary_frame():
    decoder = FrameDecoder()
    bogus = b"\x24\xFF\xFF" + bytes([CmdCode.GET_VERSION])
    ack = build_packet(CmdCode.SET_TRIG_MODE, b"\x60")

    assert decoder.feed(bogus + ack) == [ack]
    assert decoder.length_failures >= 1
    assert decoder.buffer == b""


def test_decoder_rejects_data_length_that_disagrees_with_known_pixel_count():
    decoder = FrameDecoder()
    decoder.set_expected_pixel_count(2)
    wrong_data = make_data_frame(pixels=(10, 20, 30))
    ack = build_packet(CmdCode.STOP_ACQUISITION, b"\x60")

    assert decoder.feed(wrong_data + ack) == [ack]
    assert decoder.length_failures >= 1


def test_decoder_accepts_both_data_variants_for_known_pixel_count():
    decoder = FrameDecoder()
    decoder.set_expected_pixel_count(2)
    u32_frame = make_data_frame(pixels=(10, 20))
    legacy_frame = (
        b"\x24\x07\x00\x80\x01\x00"
        b"\x00\x0A\x00\x14\x00"
    )

    assert decoder.feed(u32_frame + legacy_frame) == [u32_frame, legacy_frame]
