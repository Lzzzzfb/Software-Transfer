"""按设备导出“每帧一列、每像素一行”的批量 CSV。"""

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from ..domain.models import SpectrumFrame


def export_device_csv(
    path,
    device_id: int,
    frames: Sequence[SpectrumFrame],
    *,
    wavelengths: Sequence[float] = (),
    metadata: Mapping = None,
) -> Path:
    if not frames:
        raise ValueError("CSV 批次不能为空")
    if any(frame.device_id != device_id for frame in frames):
        raise ValueError("CSV 批次中混入了其他设备")
    pixel_count = max(frame.pixel_count for frame in frames)
    if wavelengths and len(wavelengths) != pixel_count:
        raise ValueError("波长数量与批次像素数不一致")

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    details = dict(metadata or {})
    with target.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["# ZGCAI Batch Spectrum"])
        writer.writerow(["# Device ID", device_id])
        writer.writerow(["# Frame Count", len(frames)])
        writer.writerow(["# Export Time", datetime.now(timezone.utc).isoformat()])
        for key, value in details.items():
            writer.writerow([f"# {key}", value])
        writer.writerow([])

        header = ["Pixel", "Wavelength"]
        header.extend(
            f"Frame_{index:04d}_Seq_{frame.sequence:06X}_Time_{frame.timestamp_ns}"
            for index, frame in enumerate(frames, 1)
        )
        writer.writerow(header)
        for pixel_index in range(pixel_count):
            row = [pixel_index]
            row.append(
                f"{wavelengths[pixel_index]:.6f}" if wavelengths else ""
            )
            for frame in frames:
                row.append(frame.pixels[pixel_index] if pixel_index < frame.pixel_count else "")
            writer.writerow(row)
    return target
