"""带版本、CRC32 和流式读取能力的采集临时文件。"""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import struct
import threading
import time
from typing import Dict, Iterator, List, Tuple
import zlib

import numpy as np

from ..domain.models import SpectrumFrame


MAGIC = b"ZGSPART1"
VERSION = 2
LEGACY_VERSION = 1
RECORD_MAGIC = b"FRM1"
_HEADER = struct.Struct("<8sHI")
_RECORD_HEADER = struct.Struct("<4sI")
_V1_FRAME_HEADER = struct.Struct("<IIQI")
_V2_FRAME_HEADER = struct.Struct("<IIQQBBBBIII")
_CRC = struct.Struct("<I")

_VARIANT_TO_CODE = {"model": 0, "legacy_u16": 1, "u32": 2}
_CODE_TO_VARIANT = {value: key for key, value in _VARIANT_TO_CODE.items()}
_ORDER_TO_CODE = {"little": 0, "big": 1}
_CODE_TO_DTYPE = {0: "<u2", 1: ">u2"}


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
    def __init__(
        self,
        path,
        metadata: Dict,
        flush_every: int = 10,
        fsync_interval_s: float = 1.0,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("xb")
        self._lock = threading.Lock()
        self._flush_every = max(1, int(flush_every))
        self._fsync_interval_s = max(0.0, float(fsync_interval_s))
        self._pending = 0
        self._last_fsync = time.monotonic()
        self.frame_count = 0
        metadata_bytes = json.dumps(
            metadata, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self._file.write(_HEADER.pack(MAGIC, VERSION, len(metadata_bytes)))
        self._file.write(metadata_bytes)
        self._file.write(_CRC.pack(zlib.crc32(metadata_bytes) & 0xFFFFFFFF))
        self._file.flush()

    def append(self, frame: SpectrumFrame) -> None:
        self.append_raw(
            device_id=frame.device_id,
            packet_number=frame.packet_number,
            raw_pixels=frame.pixels,
            source_pixel_count=len(frame.pixels),
            start_pixel=0,
            valid_pixel=len(frame.pixels),
            sequence_bits=frame.sequence_bits,
            protocol_variant="model",
            monotonic_ns=frame.monotonic_ns,
            timestamp_ns=frame.timestamp_ns,
        )

    def append_raw(
        self,
        *,
        device_id: int,
        packet_number: int,
        raw_pixels,
        source_pixel_count: int,
        start_pixel: int,
        valid_pixel: int,
        sequence_bits: int,
        protocol_variant: str,
        monotonic_ns: int,
        timestamp_ns: int,
    ) -> None:
        source_pixel_count = int(source_pixel_count)
        if source_pixel_count < 0:
            raise ValueError("source_pixel_count 不能为负数")
        if not 0 <= int(start_pixel) <= source_pixel_count:
            raise ValueError("start_pixel 超出原始像素范围")
        if not 0 <= int(valid_pixel) <= source_pixel_count - int(start_pixel):
            raise ValueError("valid_pixel 超出原始像素范围")
        if sequence_bits not in (16, 24):
            raise ValueError("sequence_bits 只能是 16 或 24")
        if protocol_variant not in _VARIANT_TO_CODE:
            raise ValueError(f"未知数据协议格式: {protocol_variant}")

        pixels = np.asarray(raw_pixels, dtype=">u2")
        if pixels.ndim != 1 or pixels.size != source_pixel_count:
            raise ValueError("原始像素数量与 source_pixel_count 不一致")
        pixel_bytes = pixels.tobytes(order="C")
        payload = _V2_FRAME_HEADER.pack(
            int(device_id),
            int(packet_number),
            int(monotonic_ns),
            int(timestamp_ns),
            int(sequence_bits),
            _VARIANT_TO_CODE[protocol_variant],
            _ORDER_TO_CODE["big"],
            0,
            source_pixel_count,
            int(start_pixel),
            int(valid_pixel),
        ) + pixel_bytes
        self._append_payload(payload)

    def _append_payload(self, payload: bytes) -> None:
        record = (
            _RECORD_HEADER.pack(RECORD_MAGIC, len(payload))
            + payload
            + _CRC.pack(zlib.crc32(payload) & 0xFFFFFFFF)
        )
        with self._lock:
            if self._file.closed:
                raise RuntimeError("缓存已关闭")
            self._file.write(record)
            self.frame_count += 1
            self._pending += 1
            now = time.monotonic()
            if self._pending >= self._flush_every:
                self._file.flush()
                self._pending = 0
            if (
                self._fsync_interval_s
                and now - self._last_fsync >= self._fsync_interval_s
            ):
                self._file.flush()
                os.fsync(self._file.fileno())
                self._last_fsync = now

    def flush(self, durable: bool = False) -> None:
        with self._lock:
            if not self._file.closed:
                self._file.flush()
                if durable:
                    os.fsync(self._file.fileno())
                    self._last_fsync = time.monotonic()
                self._pending = 0

    def close(self) -> None:
        with self._lock:
            if not self._file.closed:
                self._file.flush()
                os.fsync(self._file.fileno())
                self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()


def _read_header(file) -> Tuple[int, Dict]:
    header = file.read(_HEADER.size)
    if len(header) < _HEADER.size:
        raise ValueError("缓存头不完整")
    magic, version, metadata_length = _HEADER.unpack(header)
    if magic != MAGIC:
        raise ValueError("不是 ZGCAI 采集缓存")
    if version not in (LEGACY_VERSION, VERSION):
        raise ValueError(f"不支持的缓存版本: {version}")
    metadata_bytes = file.read(metadata_length)
    crc_bytes = file.read(_CRC.size)
    if len(metadata_bytes) != metadata_length or len(crc_bytes) != _CRC.size:
        raise ValueError("缓存元数据不完整")
    stored_crc = _CRC.unpack(crc_bytes)[0]
    if zlib.crc32(metadata_bytes) & 0xFFFFFFFF != stored_crc:
        raise ValueError("缓存元数据 CRC 错误")
    return version, json.loads(metadata_bytes.decode("utf-8"))


def _decode_payload(version: int, payload: bytes) -> SpectrumFrame:
    if version == LEGACY_VERSION:
        if len(payload) < _V1_FRAME_HEADER.size:
            raise ValueError("帧记录过短")
        device_id, packet_number, timestamp_ns, pixel_count = (
            _V1_FRAME_HEADER.unpack_from(payload, 0)
        )
        expected = _V1_FRAME_HEADER.size + pixel_count * 2
        if len(payload) != expected:
            raise ValueError("帧像素长度不匹配")
        pixels = np.frombuffer(
            payload, dtype="<u2", count=pixel_count, offset=_V1_FRAME_HEADER.size
        )
        return SpectrumFrame.create(
            device_id,
            packet_number,
            pixels,
            monotonic_ns=0,
            timestamp_ns=timestamp_ns,
        )

    if len(payload) < _V2_FRAME_HEADER.size:
        raise ValueError("帧记录过短")
    (
        device_id,
        packet_number,
        monotonic_ns,
        timestamp_ns,
        sequence_bits,
        variant_code,
        order_code,
        _reserved,
        source_pixel_count,
        start_pixel,
        valid_pixel,
    ) = _V2_FRAME_HEADER.unpack_from(payload, 0)
    if variant_code not in _CODE_TO_VARIANT or order_code not in _CODE_TO_DTYPE:
        raise ValueError("帧记录协议元数据无效")
    expected = _V2_FRAME_HEADER.size + source_pixel_count * 2
    if len(payload) != expected:
        raise ValueError("帧像素长度不匹配")
    if start_pixel + valid_pixel > source_pixel_count:
        raise ValueError("有效像素范围超出原始像素")
    raw = np.frombuffer(
        payload,
        dtype=_CODE_TO_DTYPE[order_code],
        count=source_pixel_count,
        offset=_V2_FRAME_HEADER.size,
    )
    valid = raw[start_pixel : start_pixel + valid_pixel]
    return SpectrumFrame.create(
        device_id,
        packet_number,
        valid,
        monotonic_ns=monotonic_ns,
        timestamp_ns=timestamp_ns,
        sequence_bits=sequence_bits,
    )


def iter_spool_frames(path) -> Iterator[SpectrumFrame]:
    """流式读取所有完整且 CRC 正确的帧；损坏记录会抛出异常。"""

    with Path(path).open("rb") as file:
        version, _metadata = _read_header(file)
        while True:
            header = file.read(_RECORD_HEADER.size)
            if not header:
                return
            if len(header) != _RECORD_HEADER.size:
                raise ValueError("尾部记录头被截断")
            marker, payload_length = _RECORD_HEADER.unpack(header)
            if marker != RECORD_MAGIC:
                raise ValueError("记录标记错误")
            payload = file.read(payload_length)
            crc_bytes = file.read(_CRC.size)
            if len(payload) != payload_length or len(crc_bytes) != _CRC.size:
                raise ValueError("尾部记录被截断")
            if zlib.crc32(payload) & 0xFFFFFFFF != _CRC.unpack(crc_bytes)[0]:
                raise ValueError("记录 CRC 错误")
            yield _decode_payload(version, payload)


def read_spool(path) -> SpoolRecovery:
    data = Path(path).read_bytes()
    issues: List[RecoveryIssue] = []
    frames: List[SpectrumFrame] = []

    if len(data) < _HEADER.size:
        raise ValueError("缓存头不完整")
    magic, version, metadata_length = _HEADER.unpack_from(data, 0)
    if magic != MAGIC:
        raise ValueError("不是 ZGCAI 采集缓存")
    if version not in (LEGACY_VERSION, VERSION):
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
        try:
            frames.append(_decode_payload(version, payload))
        except ValueError as exc:
            issues.append(RecoveryIssue(record_offset, str(exc)))
            break
        offset = record_end
        valid_bytes = offset

    return SpoolRecovery(metadata, tuple(frames), tuple(issues), valid_bytes)


def truncate_to_valid_records(path) -> SpoolRecovery:
    recovery = read_spool(path)
    if recovery.issues:
        with Path(path).open("r+b") as file:
            file.truncate(recovery.valid_bytes)
    return recovery
