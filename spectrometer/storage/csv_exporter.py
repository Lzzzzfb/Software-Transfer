"""按设备导出“每帧一列、每像素一行”的批量 CSV。"""

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from ..domain.models import SpectrumFrame
from .localization import localize_metadata_label, localize_metadata_value


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
        writer.writerow(["# ZGCAI 批量光谱数据"])
        writer.writerow(["# 设备编号", device_id])
        writer.writerow(["# 帧数", len(frames)])
        writer.writerow(["# 导出时间", datetime.now(timezone.utc).isoformat()])
        for key, value in details.items():
            writer.writerow(
                [
                    f"# {localize_metadata_label(key)}",
                    localize_metadata_value(key, value),
                ]
            )
        writer.writerow([])

        header = ["像素序号", "波长 (nm)"]
        header.extend(
            f"第 {index:04d} 帧_序号_{frame.sequence:06X}_时间_{frame.timestamp_ns}"
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
