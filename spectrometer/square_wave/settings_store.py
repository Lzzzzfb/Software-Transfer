"""Crash-safe host settings for the square-wave generator."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path

from .models import DEFAULT_BAUD_RATE, SquareWaveParameters


@dataclass(frozen=True)
class HostSquareWaveSettings:
    automatic_port: bool = True
    port_name: str = ""
    system_location: str = ""
    usb_serial: str = ""
    baud_rate: int = DEFAULT_BAUD_RATE
    parameters: SquareWaveParameters = SquareWaveParameters()

    def __post_init__(self):
        if not isinstance(self.automatic_port, bool):
            raise ValueError("automatic_port must be boolean")
        for label, value in (
            ("port_name", self.port_name),
            ("system_location", self.system_location),
            ("usb_serial", self.usb_serial),
        ):
            if not isinstance(value, str):
                raise ValueError(f"{label} must be text")
        if isinstance(self.baud_rate, bool) or self.baud_rate != DEFAULT_BAUD_RATE:
            raise ValueError("square-wave baud rate must be 9600")
        if not isinstance(self.parameters, SquareWaveParameters):
            raise ValueError("invalid square-wave parameters")


class SquareWaveSettingsStore:
    def __init__(self, path):
        self.path = Path(path)
        self.last_error = ""

    def load(self) -> HostSquareWaveSettings:
        self.last_error = ""
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("settings root is not an object")
            parameters = SquareWaveParameters(
                payload.get("frequency_hz", SquareWaveParameters().frequency_hz),
                payload.get(
                    "pulse_width_us", SquareWaveParameters().pulse_width_us
                ),
            )
            return HostSquareWaveSettings(
                automatic_port=payload.get("automatic_port", True),
                port_name=payload.get("port_name", ""),
                system_location=payload.get("system_location", ""),
                usb_serial=payload.get("usb_serial", ""),
                baud_rate=payload.get("baud_rate", DEFAULT_BAUD_RATE),
                parameters=parameters,
            )
        except FileNotFoundError:
            return HostSquareWaveSettings()
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self.last_error = str(exc) or type(exc).__name__
            return HostSquareWaveSettings()

    def save(self, settings: HostSquareWaveSettings) -> None:
        if not isinstance(settings, HostSquareWaveSettings):
            raise TypeError("invalid square-wave settings")
        payload = {
            "automatic_port": settings.automatic_port,
            "port_name": settings.port_name,
            "system_location": settings.system_location,
            "usb_serial": settings.usb_serial,
            "baud_rate": settings.baud_rate,
            "frequency_hz": settings.parameters.frequency_hz,
            "pulse_width_us": settings.parameters.pulse_width_us,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
