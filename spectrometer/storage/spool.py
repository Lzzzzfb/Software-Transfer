"""带版本和 CRC32 的采集临时缓存（.part）。"""

from dataclasses import dataclass
import json
from pathlib import Path
import struct
import threading
from typing import Dict, List, Tuple
import zlib

from ..domain.models import SpectrumFrame


MAGIC = b"ZGSPART1"
VERSION = 1
RECORD_MAGIC = b"FRM1"
_HEADER = struct.Struct("<8sHI")
_RECORD_HEADER = struct.Struct("<4sI")
_FRAME_HEADER = struct.Struct("<IIQI")
_CRC = struct.Struct("<I")


@dataclass(frozen=True)
class RecoveryIssue:
    offset: int
    message: str


@dataclass(frozen=True)
class SpoolRecovery:
    metadata: Dict
    frames: Tuple[SpectrumFrame, ...]
    issues: Tuple[RecoveryIssue, ...]
    valid_bytes: int


class SpoolWriter:
    def __init__(self, path, metadata: Dict, flush_every: int = 10):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("xb")
        self._lock = threading.Lock()
        self._flush_every = max(1, flush_every)
        self._pending = 0
        metadata_bytes = json.dumps(
            metadata, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self._file.write(_HEADER.pack(MAGIC, VERSION, len(metadata_bytes)))
        self._file.write(metadata_bytes)
        self._file.write(_CRC.pack(zlib.crc32(metadata_bytes) & 0xFFFFFFFF))
        self._file.flush()

    def append(self, frame: SpectrumFrame) -> None:
        pixel_bytes = struct.pack(
            f"<{len(frame.pixels)}H", *frame.pixels
        ) if frame.pixels else b""
        payload = _FRAME_HEADER.pack(
            frame.device_id,
            frame.packet_number,
            frame.timestamp_ns,
            len(frame.pixels),
        ) + pixel_bytes
        record = (
            _RECORD_HEADER.pack(RECORD_MAGIC, len(payload))
            + payload
            + _CRC.pack(zlib.crc32(payload) & 0xFFFFFFFF)
        )
        with self._lock:
            if self._file.closed:
                raise RuntimeError("缓存已关闭")
            self._file.write(record)
            self._pending += 1
            if self._pending >= self._flush_every:
                self._file.flush()
                self._pending = 0

    def flush(self) -> None:
        with self._lock:
            if not self._file.closed:
                self._file.flush()
                self._pending = 0

    def close(self) -> None:
        with self._lock:
            if not self._file.closed:
                self._file.flush()
                self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()


def read_spool(path) -> SpoolRecovery:
    data = Path(path).read_bytes()
    issues: List[RecoveryIssue] = []
    frames: List[SpectrumFrame] = []

    if len(data) < _HEADER.size:
        raise ValueError("缓存头不完整")
    magic, version, metadata_length = _HEADER.unpack_from(data, 0)
    if magic != MAGIC:
        raise ValueError("不是 ZGCAI 采集缓存")
    if version != VERSION:
        raise ValueError(f"不支持的缓存版本: {version}")

    offset = _HEADER.size
    metadata_end = offset + metadata_length
    if metadata_end + _CRC.size > len(data):
        raise ValueError("缓存元数据不完整")
    metadata_bytes = data[offset:metadata_end]
    stored_crc = _CRC.unpack_from(data, metadata_end)[0]
    if zlib.crc32(metadata_bytes) & 0xFFFFFFFF != stored_crc:
        raise ValueError("缓存元数据 CRC 错误")
    metadata = json.loads(metadata_bytes.decode("utf-8"))
    offset = metadata_end + _CRC.size
    valid_bytes = offset

    while offset < len(data):
        record_offset = offset
        if offset + _RECORD_HEADER.size > len(data):
            issues.append(RecoveryIssue(offset, "尾部记录头被截断"))
            break
        marker, payload_length = _RECORD_HEADER.unpack_from(data, offset)
        if marker != RECORD_MAGIC:
            issues.append(RecoveryIssue(offset, "记录标记错误"))
            break
        offset += _RECORD_HEADER.size
        record_end = offset + payload_length + _CRC.size
        if record_end > len(data):
            issues.append(RecoveryIssue(record_offset, "尾部记录被截断"))
            break
        payload = data[offset : offset + payload_length]
        stored_crc = _CRC.unpack_from(data, offset + payload_length)[0]
        if zlib.crc32(payload) & 0xFFFFFFFF != stored_crc:
            issues.append(RecoveryIssue(record_offset, "记录 CRC 错误"))
            break
        if len(payload) < _FRAME_HEADER.size:
            issues.append(RecoveryIssue(record_offset, "帧记录过短"))
            break

        device_id, packet_number, timestamp_ns, pixel_count = _FRAME_HEADER.unpack_from(
            payload, 0
        )
        expected_length = _FRAME_HEADER.size + pixel_count * 2
        if len(payload) != expected_length:
            issues.append(RecoveryIssue(record_offset, "帧像素长度不匹配"))
            break
        pixels = struct.unpack_from(f"<{pixel_count}H", payload, _FRAME_HEADER.size)
        frames.append(
            SpectrumFrame.create(
                device_id,
                packet_number,
                pixels,
                monotonic_ns=0,
                timestamp_ns=timestamp_ns,
            )
        )
        offset = record_end
        valid_bytes = offset

    return SpoolRecovery(metadata, tuple(frames), tuple(issues), valid_bytes)


def truncate_to_valid_records(path) -> SpoolRecovery:
    recovery = read_spool(path)
    if recovery.issues:
        with Path(path).open("r+b") as file:
            file.truncate(recovery.valid_bytes)
    return recovery
