import queue

from spectrometer.acquisition.process_worker import AcquisitionStreamCore
from spectrometer.acquisition.sequence_tracker import SequenceTracker
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


def test_core_keeps_only_last_ten_complete_raw_frames(tmp_path):
    core = AcquisitionStreamCore(
        0, queue.Queue(), queue.Queue(maxsize=1)
    )
    core.set_pixel_info(2, 0, 2)
    core.begin_session(tmp_path / "recent.part", {"session_id": "recent"})
    for sequence in range(12):
        core.feed(
            legacy_frame(sequence, (sequence, sequence + 1)),
            now_ns=1_000_000_000 + sequence * 100_000_000,
        )
    frames = list(core.recent_raw_frames)
    core.end_session()
    assert len(frames) == 10
    assert frames[0]["packet_number"] == 2 << 8
    assert frames[-1]["packet_number"] == 11 << 8
    assert frames[-1]["pixel_bytes"] == b"\x00\x0b\x00\x0c"


def test_display_frame_reports_frames_intentionally_skipped_by_gate(tmp_path):
    events = queue.Queue()
    display = queue.Queue(maxsize=1)
    core = AcquisitionStreamCore(0, events, display)
    core.set_pixel_info(2, 0, 2)
    core.begin_session(tmp_path / "display.part", {"session_id": "display"})
    for sequence in range(6):
        core.feed(
            legacy_frame(sequence, (1, 2)),
            now_ns=1_000_000_000 + sequence * 10_000_000,
        )
    event = display.get_nowait()
    core.end_session()

    assert event[0] == "frame"
    assert event[-1] == 4
    tracker = SequenceTracker(16)
    first = tracker.observe(0)
    second = tracker.observe(5, intentionally_skipped=event[-1])
    assert first.missing == 0
    assert second.missing == 0


def test_display_skip_count_does_not_hide_real_packet_gap(tmp_path):
    display = queue.Queue(maxsize=1)
    core = AcquisitionStreamCore(0, queue.Queue(), display)
    core.set_pixel_info(2, 0, 2)
    core.begin_session(tmp_path / "gap.part", {"session_id": "gap"})
    for index, sequence in enumerate((0, 1, 2, 4, 5)):
        core.feed(
            legacy_frame(sequence, (1, 2)),
            now_ns=1_000_000_000 + index * 13_000_000,
        )
    event = display.get_nowait()
    core.end_session()

    tracker = SequenceTracker(16)
    tracker.observe(0)
    observation = tracker.observe(5, intentionally_skipped=event[-1])
    assert event[-1] == 3
    assert observation.missing == 1
