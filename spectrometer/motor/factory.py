"""Platform boundary for the production motor controller."""

from __future__ import annotations

import sys

from .controller import MotorController
from .process_proxy import MotorProcessProxy
from .settings_store import MotorSettingsStore


def create_motor_controller(
    parent=None,
    *,
    settings_path="motor-settings.json",
    simulation=False,
    platform=None,
):
    """Use process isolation on Linux hardware, direct core elsewhere."""

    current_platform = sys.platform if platform is None else str(platform)
    if current_platform.startswith("linux") and not simulation:
        return MotorProcessProxy(
            parent,
            settings_path=settings_path,
        )
    return MotorController(
        parent,
        settings_store=MotorSettingsStore(settings_path),
    )

