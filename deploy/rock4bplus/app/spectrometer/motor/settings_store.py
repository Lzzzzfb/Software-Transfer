"""Crash-safe host settings for LK-MD2202 connection and direction mapping."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .lk_md2202 import BAUD_RATE_TO_CODE, DEFAULT_ADDRESS, DEFAULT_BAUD_RATE


@dataclass(frozen=True)
class HostMotorSettings:
    automatic_port: bool = True
    port_name: str = ""
    address: int = DEFAULT_ADDRESS
    baud_rate: int = DEFAULT_BAUD_RATE
    reverse_x: bool = False
    reverse_y: bool = False

    def __post_init__(self):
        if not 1 <= int(self.address) <= 247:
            raise ValueError("Modbus address must be between 1 and 247")
        if int(self.baud_rate) not in BAUD_RATE_TO_CODE:
            raise ValueError("unsupported baud rate")
        if not isinstance(self.automatic_port, bool):
            raise ValueError("automatic_port must be boolean")
        if not isinstance(self.reverse_x, bool) or not isinstance(self.reverse_y, bool):
            raise ValueError("direction reversal must be boolean")


class MotorSettingsStore:
    def __init__(self, path):
        self.path = Path(path)

    def load(self) -> HostMotorSettings:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("settings root is not an object")
            allowed = HostMotorSettings.__dataclass_fields__
            return HostMotorSettings(**{key: value for key, value in payload.items() if key in allowed})
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return HostMotorSettings()

    def save(self, settings: HostMotorSettings):
        settings = HostMotorSettings(**asdict(settings))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(asdict(settings), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)
