"""Convert cumulative acquisition counters into interval rates."""

from __future__ import annotations

import time


COUNTERS = (
    "raw_complete_frames",
    "persisted_frames",
    "display_frames",
    "display_overwrites",
    "missing_frames",
    "invalid_headers",
    "resync_count",
)


class AcquisitionObserver:
    def __init__(self):
        self._previous = {}

    def reset(self, device_id=None):
        if device_id is None:
            self._previous.clear()
        else:
            self._previous.pop(int(device_id), None)

    def observe(self, device_id, values, *, monotonic_ns=None):
        device_id = int(device_id)
        now = time.monotonic_ns() if monotonic_ns is None else int(monotonic_ns)
        totals = {key: int(values.get(key, 0)) for key in COUNTERS}
        previous = self._previous.get(device_id)
        deltas = {key: 0 for key in COUNTERS}
        elapsed = None
        if previous is not None:
            elapsed = max(0, now - previous["monotonic_ns"]) / 1_000_000_000
            if elapsed > 0 and all(
                totals[key] >= previous["totals"][key] for key in COUNTERS
            ):
                deltas = {
                    key: totals[key] - previous["totals"][key] for key in COUNTERS
                }
        self._previous[device_id] = {"monotonic_ns": now, "totals": totals}
        rates = {
            f"{key}_per_second": (deltas[key] / elapsed if elapsed else 0.0)
            for key in COUNTERS
        }
        return {
            "device_id": device_id,
            "elapsed_seconds": elapsed,
            "totals": totals,
            "deltas": deltas,
            "rates": rates,
            "protocol_variant": values.get("protocol_variant", ""),
        }
