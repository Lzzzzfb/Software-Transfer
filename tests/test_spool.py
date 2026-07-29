from pathlib import Path

from spectrometer.domain.models import SpectrumFrame
from spectrometer.storage.recovery import scan_pending
from spectrometer.storage.spool import (
    SpoolWriter,
    iter_spool_frames,
    read_spool,
    truncate_to_valid_records,
)


def make_frame(sequence, device_id=0):
    return SpectrumFrame.create(
        device_id,
        (sequence << 8) | 0x5A,
        [sequence, sequence + 1],
        monotonic_ns=sequence,
        timestamp_ns=1000 + sequence,
    )


def test_spool_round_trip_keeps_unicode_metadata_and_frames(tmp_path):
    path = tmp_path / "采集.part"
    with SpoolWriter(path, {"operator": "测试员", "session_id": "abc"}, flush_every=1) as writer:
        writer.append(make_frame(1))
        writer.append(make_frame(2, 3))

    recovery = read_spool(path)
    assert recovery.metadata["operator"] == "测试员"
    assert [frame.device_id for frame in recovery.frames] == [0, 3]
    assert recovery.frames[0].pixels == (1, 2)
    assert recovery.issues == ()


def test_truncated_tail_recovers_complete_records(tmp_path):
    path = tmp_path / "broken.part"
    with SpoolWriter(path, {"session_id": "broken"}, flush_every=1) as writer:
        writer.append(make_frame(1))
        writer.append(make_frame(2))
    path.write_bytes(path.read_bytes()[:-5])

    recovery = read_spool(path)
    assert len(recovery.frames) == 1
    assert recovery.issues[0].message == "尾部记录被截断"
    truncate_to_valid_records(path)
    assert read_spool(path).issues == ()


def test_scan_pending_ignores_non_spool_files(tmp_path):
    good = tmp_path / "good.part"
    with SpoolWriter(good, {"session_id": "ok"}):
        pass
    (tmp_path / "bad.part").write_text("bad", encoding="utf-8")
    assert list(scan_pending(tmp_path)) == [good]


def test_v2_raw_record_keeps_full_sensor_pixels_and_returns_valid_crop(tmp_path):
    path = tmp_path / "raw.part"
    raw = list(range(40))
    with SpoolWriter(path, {"session_id": "raw"}, flush_every=1) as writer:
        writer.append_raw(
            device_id=0,
            packet_number=1 << 8,
            raw_pixels=raw,
            source_pixel_count=40,
            start_pixel=3,
            valid_pixel=32,
            sequence_bits=16,
            protocol_variant="legacy_u16",
            monotonic_ns=123,
            timestamp_ns=456,
        )

    frames = list(iter_spool_frames(path))
    assert len(frames) == 1
    assert frames[0].pixels == tuple(range(3, 35))
    assert frames[0].sequence == 1
    assert frames[0].sequence_bits == 16
    assert frames[0].monotonic_ns == 123


def test_streaming_iterator_handles_many_records(tmp_path):
    path = tmp_path / "many.part"
    with SpoolWriter(path, {"session_id": "many"}, flush_every=50) as writer:
        for sequence in range(250):
            writer.append(make_frame(sequence))

    assert sum(1 for _ in iter_spool_frames(path)) == 250
