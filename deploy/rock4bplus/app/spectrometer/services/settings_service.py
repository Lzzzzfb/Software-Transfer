"""原子写入的用户设置持久化。"""

import json
import math
from pathlib import Path

from .platform_paths import config_directory, data_directory
from ..processing.profiles import (
    AIRPLS_ITER_MAX,
    AIRPLS_ITER_MIN,
    AIRPLS_LAM_MAX,
    AIRPLS_LAM_MIN,
    AIRPLS_ORDER_MAX,
    AIRPLS_ORDER_MIN,
)


DEFAULT_SETTINGS = {
    "storage_path": "",
    "batch_size": 500,
    "storage_format": "csv_excel",
    "auto_store": False,
    "display_mode": "raw",
    "x_axis": "pixel",
    "line_width": 1.4,
    "window_geometry": "",
    "airpls_enabled": False,
    "airpls_lam": 1e5,
    "airpls_order": 2,
    "airpls_max_iter": 15,
}


class SettingsService:
    def __init__(self, path=None):
        explicit_path = path is not None
        if path is None:
            path = config_directory() / "settings.json"
        self.path = Path(path)
        self.default_storage_path = (
            self.path.parent / "data" if explicit_path else data_directory()
        )

    def load(self):
        settings = dict(DEFAULT_SETTINGS)
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                settings.update({key: value for key, value in stored.items() if key in DEFAULT_SETTINGS})
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        settings["batch_size"] = min(1000, max(1, int(settings["batch_size"])))
        if not settings["storage_path"]:
            settings["storage_path"] = str(self.default_storage_path)
        if settings["storage_format"] not in ("csv", "excel", "csv_excel"):
            settings["storage_format"] = "csv_excel"
        if not isinstance(settings["airpls_enabled"], bool):
            settings["airpls_enabled"] = DEFAULT_SETTINGS["airpls_enabled"]
        try:
            lam = float(settings["airpls_lam"])
        except (TypeError, ValueError):
            lam = DEFAULT_SETTINGS["airpls_lam"]
        if (
            not math.isfinite(lam)
            or not AIRPLS_LAM_MIN <= lam <= AIRPLS_LAM_MAX
        ):
            lam = DEFAULT_SETTINGS["airpls_lam"]
        settings["airpls_lam"] = lam
        try:
            order = int(settings["airpls_order"])
        except (TypeError, ValueError):
            order = DEFAULT_SETTINGS["airpls_order"]
        if not AIRPLS_ORDER_MIN <= order <= AIRPLS_ORDER_MAX:
            order = DEFAULT_SETTINGS["airpls_order"]
        settings["airpls_order"] = order
        try:
            max_iter = int(settings["airpls_max_iter"])
        except (TypeError, ValueError):
            max_iter = DEFAULT_SETTINGS["airpls_max_iter"]
        if not AIRPLS_ITER_MIN <= max_iter <= AIRPLS_ITER_MAX:
            max_iter = DEFAULT_SETTINGS["airpls_max_iter"]
        settings["airpls_max_iter"] = max_iter
        return settings

    def save(self, settings):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)
