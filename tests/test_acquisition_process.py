import queue

from spectrometer.acquisition.process_worker import AcquisitionStreamCore
from spectrometer.communication.protocol import CmdCode, build_packet
from spectrometer.storage.spool import read_spool


def legacy_frame(sequence, pixels):
    pixel_bytes = b"".join(int(value).to_bytes(2, "big") for value in pixels)
    length = 3 + len(pixel_bytes)
    return (
        b"\x24"
        + length.to_bytes(2, "little")
        + bytes([CmdCode.DATA_TRANSMIT])
        + int(sequence).to_bytes(2, "little")
        + pixel_bytes
        + b"\x00"
    )


def test_core_persists_every_frame_but_limits_display(tmp_path):
    events = queue.Queue()
    display = queue.Queue(maxsize=1)
    core = AcquisitionStreamCore(0, events, display)
    core.set_pixel_info(6, 1, 4)
    path = tmp_path / "session.part"
    core.begin_session(path, {"session_id": "test"})

    for sequence in range(10):
        core.feed(
            legacy_frame(sequence, range(6)),
            now_ns=1_000_000_000 + sequence * 10_000_000,
        )
    result = core.end_session()

    recovery = read_spool(result["spool_path"])
    assert len(recovery.frames) == 10
    assert all(frame.pixels == (1, 2, 3, 4) for frame in recovery.frames)
    assert result["raw_complete_frames"] == 10
    assert result["persisted_frames"] == 10
    assert result["display_frames"] == 2
    assert result["display_overwrites"] == 8
    assert result["sealed"] is True
    assert not path.exists()


def test_core_forwards_control_response_without_display_gate():
    events = queue.Queue()
    core = AcquisitionStreamCore(0, events, queue.Queue(maxsize=1))

    core.feed(build_packet(CmdCode.STOP_ACQUISITION, b"\x60"))

    assert events.get_nowait() == (
        "response",
        0,
        int(CmdCode.STOP_ACQUISITION),
        b"\x60",
    )


def test_core_detects_missing_sequence_but_continues_persisting(tmp_path):
    events = queue.Queue()
    core = AcquisitionStreamCore(0, events, queue.Queue(maxsize=1))
    core.set_pixel_info(2, 0, 2)
    core.begin_session(tmp_path / "missing.part", {"session_id": "missing"})

    core.feed(legacy_frame(10, (1, 2)), now_ns=1_000_000_000)
    core.feed(legacy_frame(12, (3, 4)), now_ns=1_100_000_000)
    result = core.end_session()

    assert result["missing_frames"] == 1
    assert result["persisted_frames"] == 2
