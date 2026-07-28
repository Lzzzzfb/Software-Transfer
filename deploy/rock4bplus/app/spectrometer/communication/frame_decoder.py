"""与 Qt 无关的串口增量拆包器。"""

from typing import List

from .protocol import (
    HEADER_BYTE,
    CmdCode,
    calc_checksum,
    inspect_frame_header,
)


MAX_ORDINARY_FRAME_SIZE = 4096
VALID_COMMANDS = frozenset(int(command) for command in CmdCode)


class FrameDecoder:
    """把任意分片的串口字节流恢复为完整协议帧。

    候选帧在移出缓冲区前会先验证命令、长度和普通帧校验码。
    损坏候选只丢弃当前帧头字节，以便在同一数据流中重新同步。
    """

    def __init__(self, max_buffer_size: int = 2 * 1024 * 1024):
        if max_buffer_size < 4:
            raise ValueError("max_buffer_size 至少为 4")
        self.max_buffer_size = max_buffer_size
        self.buffer = bytearray()
        self.discarded_bytes = 0
        self.invalid_headers = 0
        self.overflow_count = 0
        self.checksum_failures = 0
        self.length_failures = 0
        self.resync_count = 0
        self.expected_pixel_count = 0
        self._resync_pending = False

    def reset(self) -> None:
        self.buffer.clear()
        self._resync_pending = False

    def set_expected_pixel_count(self, pixel_count: int) -> None:
        self.expected_pixel_count = max(0, int(pixel_count))

    def feed(self, chunk: bytes) -> List[bytes]:
        if chunk:
            self.buffer.extend(chunk)
            self._trim_overflow()

        frames: List[bytes] = []
        while True:
            if not self._align_header():
                break
            if len(self.buffer) < 4:
                break

            cmd = self.buffer[3]
            if cmd not in VALID_COMMANDS:
                self._reject_candidate("header")
                continue

            try:
                _, _, total_length = inspect_frame_header(self.buffer)
            except ValueError:
                self._reject_candidate("header")
                continue

            if not self._is_plausible_length(cmd, total_length):
                self._reject_candidate("length")
                continue
            if len(self.buffer) < total_length:
                break

            if cmd != int(CmdCode.DATA_TRANSMIT):
                checksum_index = total_length - 1
                expected = calc_checksum(self.buffer[1:checksum_index])
                if self.buffer[checksum_index] != expected:
                    self._reject_candidate("checksum")
                    continue

            frames.append(bytes(self.buffer[:total_length]))
            del self.buffer[:total_length]
            if self._resync_pending:
                self.resync_count += 1
                self._resync_pending = False

        return frames

    def _is_plausible_length(self, cmd: int, total_length: int) -> bool:
        if total_length > self.max_buffer_size:
            return False
        if cmd != int(CmdCode.DATA_TRANSMIT):
            return total_length <= MAX_ORDINARY_FRAME_SIZE
        if self.expected_pixel_count <= 0:
            return total_length <= 64 * 1024
        pixel_bytes = 2 * self.expected_pixel_count
        return total_length in (7 + pixel_bytes, 9 + pixel_bytes)

    def _reject_candidate(self, reason: str) -> None:
        del self.buffer[0]
        self.discarded_bytes += 1
        self.invalid_headers += 1
        self._resync_pending = True
        if reason == "checksum":
            self.checksum_failures += 1
        elif reason == "length":
            self.length_failures += 1

    def _align_header(self) -> bool:
        if not self.buffer:
            return False
        if self.buffer[0] == HEADER_BYTE:
            return True

        index = self.buffer.find(HEADER_BYTE)
        if index < 0:
            self.discarded_bytes += len(self.buffer)
            self.buffer.clear()
            self._resync_pending = True
            return False

        self.discarded_bytes += index
        del self.buffer[:index]
        self._resync_pending = True
        return True

    def _trim_overflow(self) -> None:
        if len(self.buffer) <= self.max_buffer_size:
            return
        remove_count = len(self.buffer) - self.max_buffer_size
        del self.buffer[:remove_count]
        self.discarded_bytes += remove_count
        self.overflow_count += 1
        self._resync_pending = True
