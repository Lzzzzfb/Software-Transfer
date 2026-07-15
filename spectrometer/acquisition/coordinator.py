"""把无损存储通道与约 30 fps 的显示通道解耦。"""

from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional
import threading
import time

from ..domain.enums import SyncMode
from ..domain.models import SpectrumFrame
from .sequence_tracker import SequenceObservation, SequenceTracker


@dataclass(frozen=True)
class AcquisitionDiagnostics:
    received: int
    missing: int
    duplicates: int
    out_of_order: int
    wraps: int


class AcquisitionCoordinator:
    """每帧送往存储，界面按刷新周期只取各设备最新帧。"""

    def __init__(
        self,
        storage_sink: Optional[Callable[[SpectrumFrame], None]] = None,
        display_fps: float = 30.0,
    ):
        if display_fps <= 0:
            raise ValueError("display_fps 必须大于 0")
        self.storage_sink = storage_sink
        self.display_interval_ns = int(1_000_000_000 / display_fps)
        self._latest: Dict[int, SpectrumFrame] = {}
        self._trackers: Dict[int, SequenceTracker] = {}
        self._lock = threading.Lock()
        self._last_display_ns = 0

    def ingest(self, frame: SpectrumFrame) -> SequenceObservation:
        tracker = self._trackers.get(frame.device_id)
        if tracker is None:
            tracker = SequenceTracker(frame.sequence_bits)
            self._trackers[frame.device_id] = tracker
        elif tracker.sequence_bits != frame.sequence_bits:
            raise ValueError("同一设备的包序号位宽在采集中发生变化")
        observation = tracker.observe(frame.sequence)

        # 存储回调在显示合并前调用，任何帧都不会因界面节流而丢失。
        if self.storage_sink is not None:
            self.storage_sink(frame)
        with self._lock:
            self._latest[frame.device_id] = frame
        return observation

    def take_display_frames(self, now_ns: Optional[int] = None) -> Dict[int, SpectrumFrame]:
        now = time.monotonic_ns() if now_ns is None else now_ns
        with self._lock:
            if now - self._last_display_ns < self.display_interval_ns:
                return {}
            frames = dict(self._latest)
            self._latest.clear()
            self._last_display_ns = now
        return frames

    def diagnostics(self, device_id: int) -> AcquisitionDiagnostics:
        tracker = self._trackers.get(device_id, SequenceTracker())
        return AcquisitionDiagnostics(
            received=tracker.received,
            missing=tracker.missing,
            duplicates=tracker.duplicates,
            out_of_order=tracker.out_of_order,
            wraps=tracker.wraps,
        )

    def reset(self, device_ids=None) -> None:
        """为新采集会话清空序号诊断和待显示帧。"""

        with self._lock:
            if device_ids is None:
                self._trackers.clear()
                self._latest.clear()
            else:
                for device_id in device_ids:
                    self._trackers.pop(device_id, None)
                    self._latest.pop(device_id, None)
            self._last_display_ns = 0


def acquisition_start_order(
    device_ids: Iterable[int],
    sync_mode: SyncMode,
    master_device_id: Optional[int] = None,
) -> List[int]:
    """返回布防/启动顺序；不会生成 0x54 同步脉冲命令。"""

    ids = list(dict.fromkeys(device_ids))
    if sync_mode == SyncMode.HARD_INTERNAL:
        if master_device_id not in ids:
            raise ValueError("内部硬同步必须指定有效主设备")
        return [device_id for device_id in ids if device_id != master_device_id] + [
            master_device_id
        ]
    return ids
