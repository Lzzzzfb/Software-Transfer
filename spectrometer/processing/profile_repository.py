"""Atomic per-device airPLS override repository."""

from __future__ import annotations

import json
from pathlib import Path

from .profiles import DeviceAirplsOverride, ProfileValidationError


FORMAT_VERSION = 1


class ProfileRepositoryError(RuntimeError):
    pass


def _serial(serial: str) -> str:
    value = str(serial).strip()
    if not value:
        raise ProfileRepositoryError(
            "没有可靠生产序列号，设备 airPLS 覆盖只能在当前运行期间使用"
        )
    return value


class ProcessingProfileRepository:
    def __init__(self, path):
        self.path = Path(path)

    def _read_all(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            values = json.loads(self.path.read_text(encoding="utf-8"))
            if int(values.get("format_version", 0)) != FORMAT_VERSION:
                raise ProfileRepositoryError("不支持的处理配置文件版本")
            profiles = values.get("profiles", {})
            if not isinstance(profiles, dict):
                raise ProfileRepositoryError("处理配置 profiles 必须是对象")
            return dict(profiles)
        except ProfileRepositoryError:
            raise
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProfileRepositoryError(f"处理配置读取失败：{exc}") from exc

    def _write_all(self, profiles: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temporary.write_text(
                json.dumps(
                    {
                        "format_version": FORMAT_VERSION,
                        "profiles": profiles,
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise ProfileRepositoryError(
                f"处理配置保存失败：{exc}"
            ) from exc

    def load(self, device_serial: str) -> DeviceAirplsOverride | None:
        serial = _serial(device_serial)
        values = self._read_all().get(serial)
        if values is None:
            return None
        try:
            return DeviceAirplsOverride.from_dict(values)
        except ProfileValidationError as exc:
            raise ProfileRepositoryError(
                f"设备 {serial} 的 airPLS 配置无效：{exc}"
            ) from exc

    def save(
        self, device_serial: str, profile: DeviceAirplsOverride
    ) -> Path:
        serial = _serial(device_serial)
        if not isinstance(profile, DeviceAirplsOverride):
            raise ProfileRepositoryError("设备处理配置类型错误")
        profiles = self._read_all()
        profiles[serial] = profile.to_dict()
        self._write_all(profiles)
        return self.path

    def clear(self, device_serial: str) -> bool:
        serial = _serial(device_serial)
        profiles = self._read_all()
        if serial not in profiles:
            return False
        profiles.pop(serial)
        self._write_all(profiles)
        return True
