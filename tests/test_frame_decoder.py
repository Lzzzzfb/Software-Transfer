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
