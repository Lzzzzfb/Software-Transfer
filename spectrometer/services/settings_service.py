"""原子写入的用户设置持久化。"""

import json
import os
from pathlib import Path


DEFAULT_SETTINGS = {
    "storage_path": "data",
    "batch_size": 500,
    "storage_format": "csv_excel",
    "auto_store": False,
    "display_mode": "raw",
    "x_axis": "pixel",
    "line_width": 1.4,
    "window_geometry": "",
}


class SettingsService:
    def __init__(self, path=None):
        if path is None:
            base = Path(os.environ.get("APPDATA", Path.home())) / "ZGCAI" / "Spectrometer"
            path = base / "settings.json"
        self.path = Path(path)

    def load(self):
        settings = dict(DEFAULT_SETTINGS)
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                settings.update({key: value for key, value in stored.items() if key in DEFAULT_SETTINGS})
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        settings["batch_size"] = min(1000, max(1, int(settings["batch_size"])))
        if settings["storage_format"] not in ("csv", "excel", "csv_excel"):
            settings["storage_format"] = "csv_excel"
        return settings

    def save(self, settings):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)
