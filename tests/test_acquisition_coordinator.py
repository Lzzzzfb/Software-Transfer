from spectrometer.acquisition.coordinator import (
    AcquisitionCoordinator,
    acquisition_start_order,
)
from spectrometer.domain.enums import SyncMode
from spectrometer.domain.models import SpectrumFrame


def frame(sequence, device_id=0):
    return SpectrumFrame.create(
        device_id,
        sequence << 8,
        [sequence & 0xFFFF],
        monotonic_ns=sequence,
        timestamp_ns=sequence,
    )


def test_storage_receives_every_frame_while_display_coalesces():
    stored = []
    coordinator = AcquisitionCoordinator(stored.append, display_fps=30)
    for sequence in range(100):
        coordinator.ingest(frame(sequence))

    assert len(stored) == 100
    shown = coordinator.take_display_frames(now_ns=100_000_000)
    assert shown[0].sequence == 99
    assert coordinator.take_display_frames(now_ns=110_000_000) == {}


def test_diagnostics_follow_packet_sequence():
    coordinator = AcquisitionCoordinator()
    coordinator.ingest(frame(1))
    coordinator.ingest(frame(4))
    diagnostics = coordinator.diagnostics(0)
    assert diagnostics.received == 2
    assert diagnostics.missing == 2


def test_internal_hard_sync_arms_slaves_before_master_without_pulse_command():
    assert acquisition_start_order([0, 1, 2], SyncMode.HARD_INTERNAL, 1) == [0, 2, 1]
    assert acquisition_start_order([0, 1], SyncMode.HARD_EXTERNAL) == [0, 1]
