"""无界面的 4 设备采集吞吐模拟。"""

import argparse
from pathlib import Path
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spectrometer.acquisition.coordinator import AcquisitionCoordinator
from spectrometer.domain.models import SpectrumFrame


def run(seconds: float, devices: int = 4, pixels: int = 4096, fps: int = 100):
    received = 0

    def store(frame):
        nonlocal received
        received += 1

    coordinator = AcquisitionCoordinator(store, display_fps=30)
    pixel_data = tuple(int(value) for value in np.linspace(0, 65535, pixels, dtype=np.uint16))
    total_per_device = int(seconds * fps)
    started = time.perf_counter()
    for sequence in range(total_per_device):
        for device_id in range(devices):
            coordinator.ingest(
                SpectrumFrame(
                    device_id=device_id,
                    packet_number=(sequence & 0xFFFFFF) << 8,
                    pixels=pixel_data,
                    monotonic_ns=sequence,
                    timestamp_ns=sequence,
                )
            )
        coordinator.take_display_frames(now_ns=(sequence + 1) * 10_000_000)
    elapsed = time.perf_counter() - started
    expected = devices * total_per_device
    missing = sum(coordinator.diagnostics(device_id).missing for device_id in range(devices))
    print(f"expected_frames={expected}")
    print(f"stored_frames={received}")
    print(f"sequence_gaps={missing}")
    print(f"elapsed_seconds={elapsed:.3f}")
    print(f"throughput_frames_per_second={received / max(elapsed, 1e-9):.1f}")
    if received != expected or missing:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=60.0)
    args = parser.parse_args()
    run(args.seconds)
