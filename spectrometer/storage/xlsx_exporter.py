"""多设备、多工作表 Excel 批量导出。"""

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Dict, Mapping, Sequence

import xlsxwriter

from ..domain.models import SpectrumFrame


def _safe_sheet_name(name: str, used: set) -> str:
    cleaned = re.sub(r"[\[\]:*?/\\]", "_", name).strip(" '") or "设备"
    base = cleaned[:31]
    candidate = base
    counter = 2
    while candidate.lower() in used:
        suffix = f"_{counter}"
        candidate = base[: 31 - len(suffix)] + suffix
        counter += 1
    used.add(candidate.lower())
    return candidate


def export_workbook(
    path,
    frames_by_device: Mapping[int, Sequence[SpectrumFrame]],
    *,
    wavelengths_by_device: Mapping[int, Sequence[float]] = None,
    device_labels: Mapping[int, str] = None,
    session_metadata: Mapping = None,
) -> Path:
    batches = {device_id: list(frames) for device_id, frames in frames_by_device.items()}
    if not batches or not any(batches.values()):
        raise ValueError("Excel 批次不能为空")

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    wavelengths_by_device = wavelengths_by_device or {}
    device_labels = device_labels or {}
    session_metadata = dict(session_metadata or {})

    workbook = xlsxwriter.Workbook(str(target), {"constant_memory": True})
    try:
        header_format = workbook.add_format(
            {"bold": True, "bg_color": "#DCE6F1", "border": 1}
        )
        number_format = workbook.add_format({"num_format": "0.000000"})
        summary = workbook.add_worksheet("采集概要")
        summary.set_column(0, 0, 24)
        summary.set_column(1, 1, 42)
        summary.write_row(0, 0, ["项目", "内容"], header_format)
        summary_rows = [
            ("导出时间", datetime.now(timezone.utc).isoformat()),
            ("设备数量", len(batches)),
        ] + list(session_metadata.items())
        for row, (key, value) in enumerate(summary_rows, 1):
            summary.write(row, 0, str(key))
            summary.write(row, 1, str(value))

        used = {"采集概要".lower()}
        summary_offset = len(summary_rows) + 2
        summary.write_row(summary_offset, 0, ["设备", "帧数", "像素数"], header_format)
        for summary_row, device_id in enumerate(sorted(batches), summary_offset + 1):
            frames = batches[device_id]
            label = device_labels.get(device_id, f"设备_{device_id}")
            sheet_name = _safe_sheet_name(label, used)
            sheet = workbook.add_worksheet(sheet_name)
            pixel_count = max((frame.pixel_count for frame in frames), default=0)
            wavelengths = tuple(wavelengths_by_device.get(device_id, ()))
            if wavelengths and len(wavelengths) != pixel_count:
                raise ValueError(f"设备 {device_id} 波长数量与像素数不一致")

            summary.write_row(summary_row, 0, [sheet_name, len(frames), pixel_count])
            sheet.freeze_panes(1, 2)
            sheet.set_column(0, 0, 10)
            sheet.set_column(1, 1, 14)
            sheet.set_column(2, 1 + len(frames), 22)
            sheet.write(0, 0, "Pixel", header_format)
            sheet.write(0, 1, "Wavelength", header_format)
            for column, frame in enumerate(frames, 2):
                title = f"Frame_{column - 1:04d}_Seq_{frame.sequence:06X}"
                sheet.write(0, column, title, header_format)

            # constant_memory 要求严格按行写入。
            for pixel_index in range(pixel_count):
                row = pixel_index + 1
                sheet.write_number(row, 0, pixel_index)
                if wavelengths:
                    sheet.write_number(row, 1, wavelengths[pixel_index], number_format)
                sheet.write_row(
                    row,
                    2,
                    [
                        frame.pixels[pixel_index]
                        if pixel_index < frame.pixel_count
                        else None
                        for frame in frames
                    ],
                )
    finally:
        workbook.close()
    return target
