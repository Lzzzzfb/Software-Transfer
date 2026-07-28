"""不可变的领域数据，便于在线程间安全传递和持久化。"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Tuple
import time
import uuid

from .enums import (
    AcquisitionMode,
    AcquisitionOwner,
    DeviceState,
    StorageFormat,
    SyncMode,
)


@dataclass(frozen=True)
class SpectrumFrame:
    device_id: int
    packet_number: int
    pixels: Tuple[int, ...]
    monotonic_ns: int
    timestamp_ns: int
    wavelengths: Tuple[float, ...] = ()
    sequence_bits: int = 24
    intentionally_skipped: int = 0

    def __post_init__(self):
        if self.device_id < 0:
            raise ValueError("device_id 不能为负数")
        if not 0 <= self.packet_number <= 0xFFFFFFFF:
            raise ValueError("packet_number 必须是 U32")
        if any(not 0 <= value <= 0xFFFF for value in self.pixels):
            raise ValueError("像素必须是 U16")
        if self.wavelengths and len(self.wavelengths) != len(self.pixels):
            raise ValueError("波长与像素数量必须相同")
        if self.sequence_bits not in (16, 24):
            raise ValueError("序号位宽只能是 16 或 24")
        if self.intentionally_skipped < 0:
            raise ValueError("主动跳过帧数不能为负数")

    @property
    def sequence(self) -> int:
        return (self.packet_number >> 8) & 0xFFFFFF

    @property
    def reserved(self) -> int:
        return self.packet_number & 0xFF

    @property
    def pixel_count(self) -> int:
        return len(self.pixels)

    @classmethod
    def create(
        cls,
        device_id: int,
        packet_number: int,
        pixels,
        wavelengths=(),
        *,
        monotonic_ns: Optional[int] = None,
        timestamp_ns: Optional[int] = None,
        sequence_bits: int = 24,
        intentionally_skipped: int = 0,
    ) -> "SpectrumFrame":
        return cls(
            device_id=device_id,
            packet_number=packet_number,
            pixels=tuple(int(value) for value in pixels),
            wavelengths=tuple(float(value) for value in wavelengths),
            monotonic_ns=time.monotonic_ns() if monotonic_ns is None else monotonic_ns,
            timestamp_ns=time.time_ns() if timestamp_ns is None else timestamp_ns,
            sequence_bits=sequence_bits,
            intentionally_skipped=intentionally_skipped,
        )


@dataclass(frozen=True)
class ProcessedSpectrumFrame:
    """A storage/display frame whose values may be floating point or negative."""

    device_id: int
    packet_number: int
    values: Tuple[float, ...]
    monotonic_ns: int
    timestamp_ns: int
    sequence_bits: int = 24

    def __post_init__(self):
        if self.device_id < 0:
            raise ValueError("device_id 不能为负数")
        if not 0 <= self.packet_number <= 0xFFFFFFFF:
            raise ValueError("packet_number 必须是 U32")
        if self.sequence_bits not in (16, 24):
            raise ValueError("序号位宽只能是 16 或 24")

    @property
    def sequence(self) -> int:
        return (self.packet_number >> 8) & 0xFFFFFF

    @property
    def pixel_count(self) -> int:
        return len(self.values)

    @property
    def pixels(self) -> Tuple[float, ...]:
        """Compatibility alias used by the existing exporters."""

        return self.values


@dataclass(frozen=True)
class DeviceInfo:
    device_id: int
    port_name: str
    serial_number: str = ""
    model_code: int = 0
    device_type: int = 0
    hardware_version: float = 0.0
    firmware_version: float = 0.0
    pixel_count: int = 0
    start_pixel: int = 0
    valid_pixel: int = 0
    exposure_min_us: int = 0
    exposure_max_us: int = 0

    def __post_init__(self):
        if self.pixel_count < 0 or self.start_pixel < 0 or self.valid_pixel < 0:
            raise ValueError("像素配置不能为负数")
        if self.pixel_count and self.start_pixel + self.valid_pixel > self.pixel_count:
            raise ValueError("有效像素范围超出总像素数")


@dataclass(frozen=True)
class DeviceConfig:
    integration_time_us: int = 10_000
    trigger_mode: int = 0
    interval_us: int = 0
    average_count: int = 1
    gain: int = 0
    delay_us: int = 0
    calibration: Tuple[float, float, float, float] = (0.0, 1.0, 0.0, 0.0)

    def __post_init__(self):
        if self.integration_time_us <= 0:
            raise ValueError("积分时间必须大于 0")
        if self.trigger_mode not in (0, 1, 2):
            raise ValueError("触发模式只能是 0、1、2")
        if not 1 <= self.average_count <= 10_000:
            raise ValueError("平均次数范围为 1–10000")
        if not 0 <= self.gain <= 63:
            raise ValueError("增益范围为 0–63")


@dataclass(frozen=True)
class SpectrumReference:
    reference_id: str
    device_serial: str
    pixel_count: int
    kind: str
    pixels: Tuple[float, ...]
    created_at: str

    @classmethod
    def create(cls, device_serial: str, kind: str, pixels) -> "SpectrumReference":
        values = tuple(float(value) for value in pixels)
        if kind not in ("background", "reference"):
            raise ValueError("参考类型必须是 background 或 reference")
        return cls(
            reference_id=uuid.uuid4().hex,
            device_serial=device_serial,
            pixel_count=len(values),
            kind=kind,
            pixels=values,
            created_at=datetime.now(timezone.utc).isoformat(),
        )


@dataclass(frozen=True)
class AcquisitionSession:
    session_id: str
    started_at: str
    device_ids: Tuple[int, ...]
    mode: AcquisitionMode = AcquisitionMode.CONTINUOUS
    sync_mode: SyncMode = SyncMode.INDEPENDENT
    storage_format: StorageFormat = StorageFormat.CSV_EXCEL
    batch_size: int = 500
    state: DeviceState = DeviceState.ARMED

    def __post_init__(self):
        if not self.device_ids:
            raise ValueError("采集会话至少需要一台设备")
        if not 1 <= self.batch_size <= 1000:
            raise ValueError("批量帧数范围为 1–1000")

    @classmethod
    def create(cls, device_ids, **kwargs) -> "AcquisitionSession":
        return cls(
            session_id=uuid.uuid4().hex,
            started_at=datetime.now(timezone.utc).isoformat(),
            device_ids=tuple(device_ids),
            **kwargs,
        )


@dataclass(frozen=True)
class AcquisitionRequest:
    """一次采集动作的不可变控制快照。"""

    task_id: str
    started_at: str
    owner: AcquisitionOwner
    device_ids: Tuple[int, ...]
    mode: AcquisitionMode = AcquisitionMode.CONTINUOUS
    sync_mode: SyncMode = SyncMode.INDEPENDENT
    auto_store: bool = False
    storage_format: StorageFormat = StorageFormat.CSV_EXCEL
    batch_size: int = 500
    reference_kind: Optional[str] = None

    def __post_init__(self):
        if not self.task_id:
            raise ValueError("采集任务必须具有 task_id")
        if not self.device_ids:
            raise ValueError("采集请求至少需要一台设备")
        if len(set(self.device_ids)) != len(self.device_ids):
            raise ValueError("采集请求不能包含重复设备")
        if any(device_id < 0 for device_id in self.device_ids):
            raise ValueError("device_id 不能为负数")
        if not 1 <= self.batch_size <= 1000:
            raise ValueError("批量帧数范围为 1–1000")
        if self.owner is AcquisitionOwner.CALIBRATION:
            if self.reference_kind not in ("background", "reference"):
                raise ValueError("校准任务必须指定 background 或 reference")
            if self.mode is not AcquisitionMode.SINGLE:
                raise ValueError("背景和参考必须使用单次采集")
        elif self.reference_kind is not None:
            raise ValueError("普通采集请求不能指定 reference_kind")

    @classmethod
    def create(cls, owner, device_ids, **kwargs) -> "AcquisitionRequest":
        values = dict(kwargs)
        if "mode" in values:
            values["mode"] = AcquisitionMode(values["mode"])
        if "sync_mode" in values:
            values["sync_mode"] = SyncMode(values["sync_mode"])
        if "storage_format" in values:
            values["storage_format"] = StorageFormat(values["storage_format"])
        return cls(
            task_id=uuid.uuid4().hex,
            started_at=datetime.now().astimezone().isoformat(),
            owner=AcquisitionOwner(owner),
            device_ids=tuple(int(device_id) for device_id in device_ids),
            **values,
        )
