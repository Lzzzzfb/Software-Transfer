"""与 Qt 无关的串口增量拆包器。"""

from typing import List

from .protocol import HEADER_BYTE, inspect_frame_header


class FrameDecoder:
    """把任意分片的串口字节流恢复为完整协议帧。

    解码器会自动跳过帧头前的噪声，并分别按普通帧和 0x80 数据帧的
    长度规则拆包。协议内容校验由 ``protocol`` 模块完成。
    """

    def __init__(self, max_buffer_size: int = 2 * 1024 * 1024):
        if max_buffer_size < 4:
            raise ValueError("max_buffer_size 至少为 4")
        self.max_buffer_size = max_buffer_size
        self.buffer = bytearray()
        self.discarded_bytes = 0
        self.invalid_headers = 0
        self.overflow_count = 0

    def reset(self) -> None:
        self.buffer.clear()

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

            try:
                _, _, total_length = inspect_frame_header(self.buffer)
            except ValueError:
                # 当前 0x24 很可能是噪声；跳过后继续寻找下一个帧头。
                del self.buffer[0]
                self.discarded_bytes += 1
                self.invalid_headers += 1
                continue

            if total_length > self.max_buffer_size:
                del self.buffer[0]
                self.discarded_bytes += 1
                self.invalid_headers += 1
                continue
            if len(self.buffer) < total_length:
                break

            frames.append(bytes(self.buffer[:total_length]))
            del self.buffer[:total_length]

        return frames

    def _align_header(self) -> bool:
        if not self.buffer:
            return False
        if self.buffer[0] == HEADER_BYTE:
            return True

        index = self.buffer.find(HEADER_BYTE)
        if index < 0:
            self.discarded_bytes += len(self.buffer)
            self.buffer.clear()
            return False

        self.discarded_bytes += index
        del self.buffer[:index]
        return True

    def _trim_overflow(self) -> None:
        if len(self.buffer) <= self.max_buffer_size:
            return
        remove_count = len(self.buffer) - self.max_buffer_size
        del self.buffer[:remove_count]
        self.discarded_bytes += remove_count
        self.overflow_count += 1
