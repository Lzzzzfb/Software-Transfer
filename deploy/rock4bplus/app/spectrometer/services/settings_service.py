"""原子写入的用户设置持久化。"""

import json
from pathlib import Path

from .platform_paths import config_directory, data_directory


DEFAULT_SETTINGS = {
    "storage_path": "",
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
        return settings

    def save(self, settings):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)
