"""稳定、与界面框架无关的领域对象。"""

from .enums import AcquisitionMode, DeviceState, ProcessingMode, StorageFormat, SyncMode
from .models import (
    AcquisitionSession,
    DeviceConfig,
    DeviceInfo,
    SpectrumFrame,
    SpectrumReference,
)

__all__ = [
    "AcquisitionMode",
    "AcquisitionSession",
    "DeviceConfig",
    "DeviceInfo",
    "DeviceState",
    "ProcessingMode",
    "SpectrumFrame",
    "SpectrumReference",
    "StorageFormat",
    "SyncMode",
]
