"""Pickle-safe batch export entry point executed outside the Qt process."""

import os
from pathlib import Path
from typing import Mapping

from ..processing.processor import ProcessingSnapshot, SpectrumProcessor
from .csv_exporter import export_device_csv
from .xlsx_exporter import export_workbook


def _temporary_target(target: Path) -> Path:
    return target.with_name(f".{target.name}.{os.getpid()}.tmp")


def export_processed_batch(
    batch,
    *,
    processing_snapshots: Mapping[int, ProcessingSnapshot],
    csv_targets,
    xlsx_target,
    wavelengths_by_device,
    device_labels,
    device_metadata,
    session_metadata,
):
    """Process raw frames and atomically publish all requested output files."""

    if os.name == "posix":
        try:
            os.nice(5)
        except OSError:
            pass

    processor = SpectrumProcessor()
    processed = {}
    rejected = {}
    for device_id, frames in batch.items():
        snapshot = processing_snapshots.get(device_id)
        if snapshot is None:
            raise ValueError(f"设备 {device_id} 缺少处理上下文")
        accepted = [
            processor.process_frame(frame, snapshot) for frame in frames
        ]
        if not accepted:
            raise ValueError(f"设备 {device_id} 的批次没有可导出的有效帧")
        processed[device_id] = accepted
        rejected[device_id] = []

    published = []
    temporary = []
    staged = []
    try:
        for device_id, target_value in csv_targets.items():
            target = Path(target_value)
            temp = _temporary_target(target)
            temporary.append(temp)
            metadata = dict(device_metadata.get(device_id, {}))
            snapshot = processing_snapshots[device_id]
            metadata["Processing Mode"] = snapshot.mode
            metadata["Background Applied"] = bool(snapshot.background)
            metadata["Reference Applied"] = bool(snapshot.reference)
            metadata["Rejected Frame Count"] = len(rejected.get(device_id, ()))
            export_device_csv(
                temp,
                device_id,
                processed[device_id],
                wavelengths=wavelengths_by_device.get(device_id, ()),
                metadata=metadata,
            )
            staged.append((temp, target))

        if xlsx_target:
            target = Path(xlsx_target)
            temp = _temporary_target(target)
            temporary.append(temp)
            workbook_metadata = dict(session_metadata)
            workbook_metadata["Processing Modes"] = ", ".join(
                sorted({snapshot.mode for snapshot in processing_snapshots.values()})
            )
            workbook_metadata["Background Applied"] = any(
                bool(snapshot.background)
                for snapshot in processing_snapshots.values()
            )
            workbook_metadata["Reference Applied"] = any(
                bool(snapshot.reference)
                for snapshot in processing_snapshots.values()
            )
            workbook_metadata["Rejected Frames"] = sum(map(len, rejected.values()))
            export_workbook(
                temp,
                processed,
                wavelengths_by_device=wavelengths_by_device,
                device_labels=device_labels,
                session_metadata=workbook_metadata,
            )
            staged.append((temp, target))

        for temp, target in staged:
            os.replace(temp, target)
            published.append(str(target))
    finally:
        for temp in temporary:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    return {
        "files": published,
        "rejected": {
            device_id: list(items) for device_id, items in rejected.items()
        },
    }
