"""Pure timing estimates for safe scan-segment handoff."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HandoffTimingConfig:
    near_target_pulses: int = 160
    timeout_target_pulses: int = 320
    guard_seconds: float = 0.080
    minimum_segment_seconds: float = 0.200

    def __post_init__(self):
        if self.near_target_pulses <= 0:
            raise ValueError("near-target range must be positive")
        if self.timeout_target_pulses < self.near_target_pulses:
            raise ValueError("end-phase range must cover the near-target range")
        if self.guard_seconds < 0:
            raise ValueError("handoff guard cannot be negative")
        if self.minimum_segment_seconds < 0:
            raise ValueError("minimum segment duration cannot be negative")


@dataclass(frozen=True)
class PositionSample:
    position_pulses: int
    captured_at: float


class MotionHandoffEstimator:
    """Estimate handoff only after observing trusted movement toward a target."""

    def __init__(
        self,
        *,
        start_pulses: int,
        target_pulses: int,
        commanded_at: float,
        configured_speed_pps: int,
        config: HandoffTimingConfig | None = None,
    ):
        if int(configured_speed_pps) <= 0:
            raise ValueError("configured speed must be positive")
        if int(target_pulses) == int(start_pulses):
            raise ValueError("handoff estimator requires a non-zero move")
        self.start_pulses = int(start_pulses)
        self.target_pulses = int(target_pulses)
        self.commanded_at = float(commanded_at)
        self.configured_speed_pps = int(configured_speed_pps)
        self.config = config or HandoffTimingConfig()
        self._samples: list[PositionSample] = []

    @property
    def direction(self) -> int:
        return 1 if self.target_pulses > self.start_pulses else -1

    @property
    def distance_pulses(self) -> int:
        return abs(self.target_pulses - self.start_pulses)

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    @property
    def has_trusted_motion_sample(self) -> bool:
        return bool(self._samples)

    @property
    def last_sample(self) -> PositionSample | None:
        return self._samples[-1] if self._samples else None

    @property
    def remaining_pulses(self) -> int:
        position = (
            self.last_sample.position_pulses
            if self.last_sample is not None
            else self.start_pulses
        )
        return abs(self.target_pulses - position)

    @property
    def is_near_target(self) -> bool:
        return (
            self.has_trusted_motion_sample
            and self.remaining_pulses <= self.config.near_target_pulses
        )

    @property
    def timeout_is_end_phase(self) -> bool:
        return (
            self.has_trusted_motion_sample
            and self.remaining_pulses <= self.config.timeout_target_pulses
        )

    @property
    def effective_speed_pps(self) -> float:
        if len(self._samples) < 2:
            return float(self.configured_speed_pps)
        previous, current = self._samples[-2:]
        elapsed = current.captured_at - previous.captured_at
        progress = (current.position_pulses - previous.position_pulses) * self.direction
        if elapsed <= 0 or progress <= 0:
            return float(self.configured_speed_pps)
        observed = progress / elapsed
        return min(observed, float(self.configured_speed_pps))

    def add_sample(self, position_pulses: int, captured_at: float):
        position = int(position_pulses)
        captured = float(captured_at)
        previous_time = (
            self._samples[-1].captured_at if self._samples else self.commanded_at
        )
        if captured <= previous_time:
            raise ValueError("sample time must be strictly increasing")
        progress = (position - self.start_pulses) * self.direction
        if progress <= 0:
            raise ValueError("sample moved away from target or made no progress")
        if progress > self.distance_pulses:
            raise ValueError("sample is past target")
        if self._samples:
            incremental = (
                position - self._samples[-1].position_pulses
            ) * self.direction
            if incremental <= 0:
                raise ValueError("sample moved away from target or made no progress")
        self._samples.append(PositionSample(position, captured))
        if len(self._samples) > 2:
            del self._samples[0]

    def handoff_deadline(self, calculated_at: float) -> float | None:
        sample = self.last_sample
        if sample is None:
            return None
        remaining_seconds = self.remaining_pulses / self.effective_speed_pps
        sample_deadline = (
            sample.captured_at + remaining_seconds + self.config.guard_seconds
        )
        minimum_deadline = self.commanded_at + self.config.minimum_segment_seconds
        return max(float(calculated_at), sample_deadline, minimum_deadline)

    def prediction_due(self, calculated_at: float) -> bool:
        deadline = self.handoff_deadline(calculated_at)
        return deadline is not None and deadline <= float(calculated_at)
