"""Conservative raw-frame checks for serial stream splice corruption."""

from dataclasses import dataclass

import numpy as np

from ..domain.models import SpectrumFrame


@dataclass(frozen=True)
class FrameQuality:
    accepted: bool
    suspicious: bool = False
    reason: str = ""
    low_fraction: float = 0.0
    high_fraction: float = 0.0
    rough_fraction: float = 0.0


class FrameQualityAnalyzer:
    """Detect the full-range salt-and-pepper signature seen in dropped tty data."""

    def inspect(self, frame: SpectrumFrame) -> FrameQuality:
        if frame.pixel_count < 64:
            return FrameQuality(True)
        values = np.asarray(frame.pixels, dtype=np.uint16).astype(np.int32)
        low_fraction = float(np.mean(values < 512))
        high_fraction = float(np.mean(values > 60_000))
        rough_fraction = float(np.mean(np.abs(np.diff(values)) > 10_000))
        corrupted = (
            low_fraction >= 0.002
            and high_fraction >= 0.02
            and rough_fraction >= 0.05
        )
        if not corrupted:
            return FrameQuality(
                True,
                low_fraction=low_fraction,
                high_fraction=high_fraction,
                rough_fraction=rough_fraction,
            )
        return FrameQuality(
            False,
            suspicious=True,
            reason=(
                "原始帧呈现全量程离散跳变"
                f"（低值 {low_fraction:.1%}，饱和值 {high_fraction:.1%}，"
                f"剧烈相邻跳变 {rough_fraction:.1%}）"
            ),
            low_fraction=low_fraction,
            high_fraction=high_fraction,
            rough_fraction=rough_fraction,
        )
