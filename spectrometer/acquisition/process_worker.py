"""Linux 串口采集子进程：控制、全量落盘与低频显示分流。"""

from collections import deque
import hashlib
import queue
import os
from pathlib import Path
import shutil
import time
from typing import Optional

import numpy as np

from ..communication.frame_decoder import FrameDecoder
from ..communication.protocol import (
    CmdCode,
    build_packet,
    parse_data_packet_view,
    parse_packet,
)
from ..storage.spool import SpoolWriter
from .health_monitor import AcquisitionHealthMonitor


DISPLAY_INTERVAL_NS = 40_000_000
DIAGNOSTIC_INTERVAL_NS = 1_000_000_000
MINIMUM_FREE_BYTES = 256 * 1024 * 1024


def _put_event(event_queue, event) -> None:
    try:
        event_queue.put_nowait(event)
    except queue.Full:
        # 控制事件队列满表示父进程已经无法可靠监管采集。
        raise RuntimeError("采集控制事件队列已满")


def _replace_latest(display_queue, event) -> None:
    try:
        display_queue.put_nowait(event)
        return
    except queue.Full:
        pass
    replaced = None
    try:
        replaced = display_queue.get_nowait()
    except queue.Empty:
        pass
    if (
        isinstance(event, tuple)
        and event
        and event[0] == "frame"
        and isinstance(replaced, tuple)
        and replaced
        and replaced[0] == "frame"
    ):
        values = list(event)
        values[-1] = int(values[-1]) + int(replaced[-1]) + 1
        event = tuple(values)
    try:
        display_queue.put_nowait(event)
    except queue.Full:
        pass


class AcquisitionStreamCore:
    """与串口实现无关的帧处理核心，便于在桌面测试。"""

    def __init__(self, device_id: int, event_queue, display_queue):
        self.device_id = int(device_id)
        self.event_queue = event_queue
        self.display_queue = display_queue
        self.decoder = FrameDecoder()
        self.n_pixel = 0
        self.n_start_pixel = 0
        self.n_valid_pixel = 0
        self.spool: Optional[SpoolWriter] = None
        self.spool_path = ""
        self.raw_complete_frames = 0
        self.persisted_frames = 0
        self.display_frames = 0
        self.display_overwrites = 0
        self.missing_frames = 0
        self.last_sequence = None
        self.last_display_ns = 0
        self.last_diagnostic_ns = 0
        self.health = AcquisitionHealthMonitor()
        self.health_stop_sent = False
        self.recent_raw_frames = deque(maxlen=10)
        self.intentionally_skipped = 0

    def set_pixel_info(self, n_pixel: int, start: int, valid: int) -> None:
        self.n_pixel = int(n_pixel)
        self.n_start_pixel = int(start)
        self.n_valid_pixel = int(valid)
        self.decoder.set_expected_pixel_count(self.n_pixel)

    def begin_session(self, path, metadata) -> None:
        if self.spool is not None:
            raise RuntimeError("采集会话已经打开")
        parent = Path(path).parent
        parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(parent).free < MINIMUM_FREE_BYTES:
            raise OSError("采集数据目录可用空间低于 256 MiB")
        self.spool = SpoolWriter(path, dict(metadata), flush_every=10)
        self.spool_path = str(path)
        self.persisted_frames = 0
        self.raw_complete_frames = 0
        self.missing_frames = 0
        self.last_sequence = None
        self.recent_raw_frames.clear()
        self.intentionally_skipped = 0

    def end_session(self, seal: bool = True) -> dict:
        if self.spool is not None:
            self.spool.close()
            self.spool = None
        result = self.diagnostics()
        path = self.spool_path
        if seal and path:
            source = Path(path)
            target = source.with_suffix(".zgs")
            os.replace(source, target)
            path = str(target)
            self.spool_path = path
        result["spool_path"] = path
        result["sealed"] = bool(seal)
        return result

    def feed(self, chunk: bytes, now_ns: Optional[int] = None) -> None:
        before_invalid = self.decoder.invalid_headers
        before_resync = self.decoder.resync_count
        for packet in self.decoder.feed(chunk):
            if packet[3] == int(CmdCode.DATA_TRANSMIT):
                self._handle_data(packet, now_ns)
            else:
                cmd, params = parse_packet(packet)
                _put_event(
                    self.event_queue,
                    ("response", self.device_id, int(cmd), bytes(params)),
                )
        rejected = self.decoder.invalid_headers - before_invalid
        recovered = self.decoder.resync_count - before_resync
        if rejected:
            _put_event(
                self.event_queue,
                (
                    "stream_error",
                    self.device_id,
                    {
                        "rejected_candidates": rejected,
                        "recovered": recovered,
                        "checksum_failures": self.decoder.checksum_failures,
                        "length_failures": self.decoder.length_failures,
                    },
                ),
            )

    def _handle_data(self, packet: bytes, now_ns: Optional[int]) -> None:
        parsed = parse_data_packet_view(
            packet,
            n_pixel=self.n_pixel,
            n_start_pixel=self.n_start_pixel,
            n_valid_pixel=self.n_valid_pixel,
        )
        now = time.monotonic_ns() if now_ns is None else int(now_ns)
        timestamp = time.time_ns()
        sequence = int(parsed["frame_sequence"])
        sequence_bits = 16 if parsed["packet_number_bits"] == 16 else 24
        model_packet_number = (
            sequence << 8
            if sequence_bits == 16
            else int(parsed["packet_number"])
        )
        self.raw_complete_frames += 1
        missing = self._track_sequence(sequence, sequence_bits)
        decision = self.health.observe(now, valid=1, abnormal=missing)
        if decision.stop and not self.health_stop_sent:
            self.health_stop_sent = True
            _put_event(
                self.event_queue,
                ("controlled_stop", self.device_id, decision.reason),
            )

        if self.spool is None:
            raise RuntimeError("收到采集数据但持久化会话尚未开始")
        self.spool.append_raw(
            device_id=self.device_id,
            packet_number=model_packet_number,
            raw_pixels=parsed["raw_pixels"],
            source_pixel_count=parsed["source_pixel_count"],
            start_pixel=self.n_start_pixel,
            valid_pixel=parsed["pixel_count"],
            sequence_bits=sequence_bits,
            protocol_variant=parsed["protocol_variant"],
            monotonic_ns=now,
            timestamp_ns=timestamp,
        )
        self.persisted_frames += 1
        raw_bytes = np.asarray(parsed["raw_pixels"], dtype=">u2").tobytes()
        self.recent_raw_frames.append(
            {
                "device_id": self.device_id,
                "packet_number": model_packet_number,
                "sequence_bits": sequence_bits,
                "monotonic_ns": now,
                "timestamp_ns": timestamp,
                "source_pixel_count": parsed["source_pixel_count"],
                "start_pixel": self.n_start_pixel,
                "valid_pixel": parsed["pixel_count"],
                "protocol_variant": parsed["protocol_variant"],
                "pixel_bytes": raw_bytes,
            }
        )

        if not self.last_display_ns or now - self.last_display_ns >= DISPLAY_INTERVAL_NS:
            pixels = np.asarray(parsed["pixels"], dtype="<u2").tobytes()
            _replace_latest(
                self.display_queue,
                (
                    "frame",
                    self.device_id,
                    model_packet_number,
                    sequence_bits,
                    now,
                    timestamp,
                    pixels,
                    parsed["pixel_count"],
                    self.intentionally_skipped,
                ),
            )
            self.display_frames += 1
            self.last_display_ns = now
            self.intentionally_skipped = 0
        else:
            self.display_overwrites += 1
            self.intentionally_skipped += 1

        if now - self.last_diagnostic_ns >= DIAGNOSTIC_INTERVAL_NS:
            if (
                self.spool_path
                and shutil.disk_usage(Path(self.spool_path).parent).free
                < MINIMUM_FREE_BYTES
                and not self.health_stop_sent
            ):
                self.health_stop_sent = True
                _put_event(
                    self.event_queue,
                    (
                        "controlled_stop",
                        self.device_id,
                        "采集数据目录可用空间低于 256 MiB",
                    ),
                )
            _put_event(
                self.event_queue,
                ("diagnostics", self.device_id, self.diagnostics()),
            )
            pixels64 = np.asarray(parsed["raw_pixels"], dtype=np.float64)
            _put_event(
                self.event_queue,
                (
                    "frame_summary",
                    self.device_id,
                    {
                        "device_id": self.device_id,
                        "packet_number": model_packet_number,
                        "sequence_bits": sequence_bits,
                        "frame_length": len(packet),
                        "source_pixel_count": parsed["source_pixel_count"],
                        "start_pixel": self.n_start_pixel,
                        "valid_pixel": parsed["pixel_count"],
                        "protocol_variant": parsed["protocol_variant"],
                        "minimum": float(pixels64.min()),
                        "maximum": float(pixels64.max()),
                        "mean": float(pixels64.mean()),
                        "standard_deviation": float(pixels64.std()),
                        "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                    },
                ),
            )
            self.last_diagnostic_ns = now

    def _track_sequence(self, sequence: int, bits: int) -> int:
        missing = 0
        if self.last_sequence is not None:
            modulus = 1 << bits
            delta = (sequence - self.last_sequence) % modulus
            if 1 < delta < modulus // 2:
                missing = delta - 1
                self.missing_frames += missing
        self.last_sequence = sequence
        return missing

    def diagnostics(self) -> dict:
        return {
            "raw_complete_frames": self.raw_complete_frames,
            "persisted_frames": self.persisted_frames,
            "display_frames": self.display_frames,
            "display_overwrites": self.display_overwrites,
            "missing_frames": self.missing_frames,
            "protocol_variant": self.decoder.data_variant or "",
            "invalid_headers": self.decoder.invalid_headers,
            "resync_count": self.decoder.resync_count,
        }


def _serial_enum(serial_type, group_name: str, value_name: str):
    if hasattr(serial_type, value_name):
        return getattr(serial_type, value_name)
    return getattr(getattr(serial_type, group_name), value_name)


def acquisition_process_main(
    control,
    event_queue,
    display_queue,
    device_id: int,
    port_name: str,
    baud_rate: int,
) -> None:
    """子进程入口；仅由 ``AcquisitionProcessProxy`` 使用。"""

    from ..qt import QtCore, QtSerialPort

    application = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication(
        [f"zgcai-acquisition-{device_id}"]
    )
    serial_type = QtSerialPort.QSerialPort
    serial = serial_type()
    serial.setPortName(port_name)
    serial.setBaudRate(int(baud_rate))
    serial.setDataBits(_serial_enum(serial_type, "DataBits", "Data8"))
    serial.setParity(_serial_enum(serial_type, "Parity", "NoParity"))
    serial.setStopBits(_serial_enum(serial_type, "StopBits", "OneStop"))
    serial.setFlowControl(_serial_enum(serial_type, "FlowControl", "NoFlowControl"))
    if hasattr(serial_type, "ReadWrite"):
        mode = serial_type.ReadWrite
    elif hasattr(QtCore.QIODevice, "ReadWrite"):
        mode = QtCore.QIODevice.ReadWrite
    else:
        mode = QtCore.QIODevice.OpenModeFlag.ReadWrite

    core = AcquisitionStreamCore(device_id, event_queue, display_queue)
    try:
        if not serial.open(mode):
            _put_event(
                event_queue,
                ("open", device_id, False, serial.errorString()),
            )
            return
        serial.setReadBufferSize(4 * 1024 * 1024)
        _put_event(event_queue, ("open", device_id, True, ""))
        running = True
        while running:
            while control.poll():
                message = control.recv()
                kind = message[0]
                if kind == "command":
                    _, cmd, params = message
                    packet = build_packet(int(cmd), bytes(params))
                    if serial.write(packet) != len(packet):
                        raise OSError("串口命令未完整写入")
                    serial.waitForBytesWritten(500)
                elif kind == "pixel_info":
                    core.set_pixel_info(*message[1:])
                elif kind == "begin_session":
                    core.begin_session(message[1], message[2])
                    _put_event(
                        event_queue,
                        ("session_started", device_id, str(message[1])),
                    )
                elif kind == "end_session":
                    _put_event(
                        event_queue,
                        ("session_sealed", device_id, core.end_session()),
                    )
                elif kind == "recent_frames":
                    _put_event(
                        event_queue,
                        ("recent_frames", device_id, list(core.recent_raw_frames)),
                    )
                elif kind == "close":
                    running = False
                    break
            if not running:
                break
            if serial.waitForReadyRead(5):
                core.feed(bytes(serial.readAll()))
                while serial.bytesAvailable():
                    core.feed(bytes(serial.readAll()))
    except Exception as exc:
        try:
            _put_event(event_queue, ("fatal", device_id, str(exc)))
        except Exception:
            pass
    finally:
        try:
            if core.spool is not None:
                result = core.end_session(seal=False)
                _put_event(event_queue, ("session_sealed", device_id, result))
        except Exception:
            pass
        serial.close()
        control.close()
