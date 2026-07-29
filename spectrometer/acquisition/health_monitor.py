"""采集流异常窗口与受控停止阈值。"""

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class HealthDecision:
    stop: bool
    reason: str = ""
    abnormal_ratio: float = 0.0


class AcquisitionHealthMonitor:
    def __init__(
        self,
        *,
        window_ns: int = 10_000_000_000,
        ratio_limit: float = 0.05,
        consecutive_limit: int = 5,
    ):
        self.window_ns = int(window_ns)
        self.ratio_limit = float(ratio_limit)
        self.consecutive_limit = int(consecutive_limit)
        self._events = deque()
        self._total = 0
        self._abnormal = 0
        self._consecutive = 0

    def observe(self, now_ns: int, *, valid: int = 1, abnormal: int = 0):
        valid = max(0, int(valid))
        abnormal = max(0, int(abnormal))
        total = valid + abnormal
        self._events.append((int(now_ns), total, abnormal))
        self._total += total
        self._abnormal += abnormal
        self._consecutive = (
            self._consecutive + abnormal if abnormal else 0
        )
        self._expire(int(now_ns))
        ratio = self._abnormal / self._total if self._total else 0.0
        if self._consecutive >= self.consecutive_limit:
            return HealthDecision(
                True,
                f"连续异常帧达到 {self._consecutive}",
                ratio,
            )
        if self._total >= 20 and ratio >= self.ratio_limit:
            return HealthDecision(
                True,
                f"10秒窗口异常比例达到 {ratio:.2%}",
                ratio,
            )
        return HealthDecision(False, abnormal_ratio=ratio)

    def _expire(self, now_ns: int) -> None:
        cutoff = now_ns - self.window_ns
        while self._events and self._events[0][0] < cutoff:
            _, total, abnormal = self._events.popleft()
            self._total -= total
            self._abnormal -= abnormal
