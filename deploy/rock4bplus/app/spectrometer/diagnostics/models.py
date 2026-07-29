"""Stable diagnostic serialization helpers."""

from __future__ import annotations

from datetime import datetime
import json
import time
from typing import Any


FORMAT_VERSION = 1


def timestamp_fields() -> dict[str, Any]:
    return {
        "local_time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "monotonic_ns": time.monotonic_ns(),
    }


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if hasattr(value, "value"):
        return json_safe(value.value)
    return str(value)


def json_line(kind: str, payload: dict[str, Any]) -> str:
    record = {"format_version": FORMAT_VERSION, "kind": kind, **timestamp_fields()}
    record.update(json_safe(payload))
    return json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
