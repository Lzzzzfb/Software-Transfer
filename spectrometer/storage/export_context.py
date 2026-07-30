"""采集停止后仍可安全使用的不可变导出上下文。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Tuple

from ..domain.models import AcquisitionRequest
from ..processing.processor import ProcessingSnapshot


@dataclass(frozen=True)
class FrozenDeviceExport:
    device_id: int
    spool_path: Path
    expected_count: int
    wavelengths: Tuple[float, ...]
    label: str
    metadata_items: Tuple[Tuple[str, object], ...]
    processing_snapshot: ProcessingSnapshot
    filename_stem: str

    @property
    def metadata(self) -> dict:
        return dict(self.metadata_items)


@dataclass(frozen=True)
class SealedExportContext:
    request: AcquisitionRequest
    output_directory: Path
    filename_stem: str
    devices: Tuple[FrozenDeviceExport, ...]

    def __post_init__(self):
        expected = set(self.request.device_ids)
        actual = {device.device_id for device in self.devices}
        if expected != actual:
            raise ValueError("封存导出上下文的设备集合与采集请求不一致")

    @property
    def spool_paths(self) -> Mapping[int, Path]:
        return {
            device.device_id: device.spool_path
            for device in self.devices
        }

    @property
    def expected_counts(self) -> Mapping[int, int]:
        return {
            device.device_id: device.expected_count
            for device in self.devices
        }

    @property
    def wavelengths_by_device(self) -> Mapping[int, tuple]:
        return {
            device.device_id: device.wavelengths
            for device in self.devices
        }

    @property
    def device_labels(self) -> Mapping[int, str]:
        return {
            device.device_id: device.label
            for device in self.devices
        }

    @property
    def device_metadata(self) -> Mapping[int, dict]:
        return {
            device.device_id: device.metadata
            for device in self.devices
        }

    @property
    def processing_snapshots(self) -> Mapping[int, ProcessingSnapshot]:
        return {
            device.device_id: device.processing_snapshot
            for device in self.devices
        }

    @property
    def device_filename_stems(self) -> Mapping[int, str]:
        return {
            device.device_id: device.filename_stem
            for device in self.devices
        }
