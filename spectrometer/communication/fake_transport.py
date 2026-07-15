"""供无硬件测试和模拟模式使用的确定性传输替身。"""

from typing import Callable, List, Optional, Tuple


class FakeTransport:
    def __init__(self):
        self.connected = False
        self.writes: List[bytes] = []
        self.on_bytes: Optional[Callable[[bytes], None]] = None

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.connected = False

    def write(self, data: bytes) -> None:
        if not self.connected:
            raise RuntimeError("模拟传输尚未连接")
        self.writes.append(bytes(data))

    def inject(self, data: bytes, chunk_sizes: Tuple[int, ...] = ()) -> None:
        if self.on_bytes is None:
            return
        if not chunk_sizes:
            self.on_bytes(bytes(data))
            return
        offset = 0
        for size in chunk_sizes:
            self.on_bytes(bytes(data[offset : offset + size]))
            offset += size
        if offset < len(data):
            self.on_bytes(bytes(data[offset:]))
