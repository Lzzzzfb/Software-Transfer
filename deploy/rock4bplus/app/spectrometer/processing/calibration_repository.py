"""Atomic repository for per-device intensity calibration records."""

from __future__ import annotations

import json
from pathlib import Path
import re

from .intensity_calibration import (
    IntensityCalibration,
    IntensityCalibrationError,
    validate_intensity_calibration,
)


class CalibrationRepositoryError(RuntimeError):
    pass


_UNSAFE = re.compile(r"[^0-9A-Za-z._-]+")


def _serial_token(serial: str) -> str:
    token = _UNSAFE.sub("_", str(serial).strip()).strip("._")
    if not token:
        raise CalibrationRepositoryError(
            "没有可靠生产序列号，强度校准只能在当前运行期间使用"
        )
    return token


class IntensityCalibrationRepository:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, device_serial: str, pixel_count: int) -> Path:
        token = _serial_token(device_serial)
        count = int(pixel_count)
        if count <= 0:
            raise CalibrationRepositoryError("有效像素数必须大于 0")
        return self.directory / f"{token}_{count}.json"

    def save(self, item: IntensityCalibration) -> Path:
        try:
            validate_intensity_calibration(item)
            target = self.path_for(item.device_serial, item.pixel_count)
        except (IntensityCalibrationError, ValueError) as exc:
            raise CalibrationRepositoryError(str(exc)) from exc
        temporary = target.with_suffix(".json.tmp")
        try:
            temporary.write_text(
                json.dumps(
                    item.to_dict(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            temporary.replace(target)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise CalibrationRepositoryError(
                f"强度校准记录保存失败：{exc}"
            ) from exc
        return target

    def load(
        self, device_serial: str, pixel_count: int
    ) -> IntensityCalibration | None:
        target = self.path_for(device_serial, pixel_count)
        if not target.exists():
            return None
        try:
            values = json.loads(target.read_text(encoding="utf-8"))
            item = IntensityCalibration.from_dict(values)
        except (
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            IntensityCalibrationError,
        ) as exc:
            raise CalibrationRepositoryError(
                f"强度校准记录读取失败：{exc}"
            ) from exc
        if (
            item.device_serial != str(device_serial).strip()
            or item.pixel_count != int(pixel_count)
        ):
            raise CalibrationRepositoryError(
                "强度校准记录与设备序列号或有效像素数不匹配"
            )
        return item

    def clear(self, device_serial: str, pixel_count: int) -> bool:
        target = self.path_for(device_serial, pixel_count)
        if not target.exists():
            return False
        try:
            target.unlink()
        except OSError as exc:
            raise CalibrationRepositoryError(
                f"强度校准记录清除失败：{exc}"
            ) from exc
        return True
