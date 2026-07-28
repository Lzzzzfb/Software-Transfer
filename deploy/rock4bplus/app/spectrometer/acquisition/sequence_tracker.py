"""16/24 位采集序号连续性诊断。"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SequenceObservation:
    sequence: int
    previous: Optional[int]
    missing: int = 0
    duplicate: bool = False
    out_of_order: bool = False
    wrapped: bool = False


class SequenceTracker:
    def __init__(self, sequence_bits: int = 24):
        if sequence_bits not in (16, 24):
            raise ValueError("序号位宽只能是 16 或 24")
        self.sequence_bits = sequence_bits
        self.modulus = 1 << sequence_bits
        self.half_range = 1 << (sequence_bits - 1)
        self.last_sequence: Optional[int] = None
        self.received = 0
        self.missing = 0
        self.duplicates = 0
        self.out_of_order = 0
        self.wraps = 0

    def observe(
        self, sequence: int, intentionally_skipped: int = 0
    ) -> SequenceObservation:
        if not 0 <= sequence < self.modulus:
            raise ValueError(f"序号必须是 {self.sequence_bits} 位无符号整数")
        if intentionally_skipped < 0:
            raise ValueError("主动跳过帧数不能为负数")

        previous = self.last_sequence
        self.received += 1
        if previous is None:
            self.last_sequence = sequence
            return SequenceObservation(sequence=sequence, previous=None)

        delta = (sequence - previous) % self.modulus
        if delta == 0:
            self.duplicates += 1
            return SequenceObservation(
                sequence=sequence, previous=previous, duplicate=True
            )
        if delta >= self.half_range:
            self.out_of_order += 1
            return SequenceObservation(
                sequence=sequence, previous=previous, out_of_order=True
            )

        missing = max(0, delta - 1 - intentionally_skipped)
        wrapped = sequence < previous
        self.missing += missing
        self.wraps += int(wrapped)
        self.last_sequence = sequence
        return SequenceObservation(
            sequence=sequence,
            previous=previous,
            missing=missing,
            wrapped=wrapped,
        )

    def reset(self) -> None:
        self.__init__(self.sequence_bits)
