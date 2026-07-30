"""Crash-safe persistence for host-estimated motor coordinates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

from .models import Position


STATE_VERSION = 1


@dataclass(frozen=True)
class StoredMotorState:
    device_id: str
    position: Position
    valid: bool
    dirty: bool
    reason: str
    updated_at: str

    @property
    def trusted(self) -> bool:
        return self.valid and not self.dirty


class MotorStateStore:
    def __init__(self, path):
        self.path = Path(path)

    def _read_payload(self) -> dict:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {"version": STATE_VERSION, "devices": {}}
        if (
            not isinstance(payload, dict)
            or payload.get("version") != STATE_VERSION
            or not isinstance(payload.get("devices"), dict)
        ):
            return {"version": STATE_VERSION, "devices": {}}
        return payload

    def _write_payload(self, payload: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()

    def load(self, device_id: str) -> StoredMotorState | None:
        record = self._read_payload()["devices"].get(str(device_id))
        if not isinstance(record, dict):
            return None
        try:
            position = Position(
                float(record["position"]["x_mm"]),
                float(record["position"]["y_mm"]),
                float(record["position"].get("z_mm", 0.0)),
            )
            return StoredMotorState(
                device_id=str(device_id),
                position=position,
                valid=bool(record.get("valid", False)),
                dirty=bool(record.get("dirty", True)),
                reason=str(record.get("reason") or ""),
                updated_at=str(record.get("updated_at") or ""),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _update(
        self,
        device_id: str,
        *,
        position: Position | None = None,
        valid: bool,
        dirty: bool,
        reason: str,
    ):
        device_id = str(device_id)
        payload = self._read_payload()
        previous = self.load(device_id)
        resolved_position = (
            position
            if position is not None
            else (
                previous.position
                if previous is not None
                else Position(0.0, 0.0, 0.0)
            )
        )
        payload["devices"][device_id] = {
            "position": {
                "x_mm": resolved_position.x_mm,
                "y_mm": resolved_position.y_mm,
                "z_mm": resolved_position.z_mm,
            },
            "valid": bool(valid),
            "dirty": bool(dirty),
            "reason": str(reason),
            "updated_at": self._timestamp(),
        }
        self._write_payload(payload)

    def begin_motion(self, device_id: str):
        previous = self.load(device_id)
        self._update(
            device_id,
            position=previous.position if previous is not None else None,
            valid=previous.valid if previous is not None else False,
            dirty=True,
            reason="motion_in_progress",
        )

    def confirm_position(self, device_id: str, position: Position):
        self._update(
            device_id,
            position=position,
            valid=True,
            dirty=False,
            reason="confirmed",
        )

    def invalidate(self, device_id: str, reason: str):
        previous = self.load(device_id)
        self._update(
            device_id,
            position=previous.position if previous is not None else None,
            valid=False,
            dirty=previous.dirty if previous is not None else True,
            reason=reason,
        )
