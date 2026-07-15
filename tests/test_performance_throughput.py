from spectrometer.acquisition.coordinator import AcquisitionCoordinator
from spectrometer.domain.models import SpectrumFrame


def test_four_devices_4096_pixels_storage_path_has_no_display_drop_coupling():
    stored = 0

    def sink(frame):
        nonlocal stored
        stored += 1

    coordinator = AcquisitionCoordinator(sink, display_fps=30)
    pixels = tuple(range(4096))
    for sequence in range(1000):  # 相当于 4 台设备各 100 fps 持续 10 秒
        for device_id in range(4):
            coordinator.ingest(
                SpectrumFrame(device_id, sequence << 8, pixels, sequence, sequence)
            )
        coordinator.take_display_frames(now_ns=(sequence + 1) * 10_000_000)

    assert stored == 4000
    assert all(coordinator.diagnostics(device_id).missing == 0 for device_id in range(4))
