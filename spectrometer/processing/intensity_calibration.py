"""Per-pixel intensity calibration records and CSV/TXT import."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Tuple


FORMAT_VERSION = 1


class IntensityCalibrationError(ValueError):
    pass


@dataclass(frozen=True)
class IntensityCalibration:
    calibration_id: str
    device_serial: str
    pixel_count: int
    coefficients: Tuple[float, ...]
    imported_at: str
    source_name: str
    source_sha256: str
    format_version: int = FORMAT_VERSION

    def to_dict(self) -> dict:
        return {
            "format_version": self.format_version,
            "calibration_id": self.calibration_id,
            "device_serial": self.device_serial,
            "pixel_count": self.pixel_count,
            "coefficients": list(self.coefficients),
            "imported_at": self.imported_at,
            "source_name": self.source_name,
            "source_sha256": self.source_sha256,
        }

    @classmethod
    def from_dict(cls, values) -> "IntensityCalibration":
        try:
            item = cls(
                calibration_id=str(values["calibration_id"]),
                device_serial=str(values["device_serial"]),
                pixel_count=int(values["pixel_count"]),
                coefficients=tuple(
                    float(value) for value in values["coefficients"]
                ),
                imported_at=str(values["imported_at"]),
                source_name=str(values["source_name"]),
                source_sha256=str(values["source_sha256"]),
                format_version=int(values["format_version"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise IntensityCalibrationError(f"强度校准记录格式错误：{exc}") from exc
        validate_intensity_calibration(item)
        if item.calibration_id != _record_id(
            item.device_serial,
            item.pixel_count,
            item.coefficients,
            item.source_sha256,
        ):
            raise IntensityCalibrationError("强度校准记录 ID 校验失败")
        return item


def _decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise IntensityCalibrationError(
        "强度校准文件编码无法识别，仅支持 UTF-8、UTF-8 BOM 或 GB18030"
    )


def _tokens(line: str):
    if "," in line:
        values = [value.strip() for value in line.split(",")]
    elif "\t" in line:
        values = [value.strip() for value in line.split("\t")]
    else:
        values = line.split()
    if len(values) not in (1, 2):
        raise IntensityCalibrationError("强度校准文件每行只能包含一列或两列")
    if any(value == "" for value in values):
        raise IntensityCalibrationError("强度校准文件包含空字段")
    return values


def _is_numeric_row(values) -> bool:
    try:
        if len(values) == 2:
            int(values[0])
        float(values[-1])
    except ValueError:
        return False
    return True


def _record_id(serial, pixel_count, coefficients, source_sha256) -> str:
    canonical = json.dumps(
        {
            "device_serial": str(serial),
            "pixel_count": int(pixel_count),
            "coefficients": [float(value) for value in coefficients],
            "source_sha256": str(source_sha256),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:16]


def validate_intensity_calibration(item: IntensityCalibration) -> None:
    if item.format_version != FORMAT_VERSION:
        raise IntensityCalibrationError(
            f"不支持的强度校准格式版本：{item.format_version}"
        )
    if item.pixel_count <= 0:
        raise IntensityCalibrationError("设备有效像素数必须大于 0")
    if len(item.coefficients) != item.pixel_count:
        raise IntensityCalibrationError(
            f"强度校准系数数量 {len(item.coefficients)} "
            f"与有效像素数 {item.pixel_count} 不一致"
        )
    if any(
        not math.isfinite(float(value)) or float(value) <= 0
        for value in item.coefficients
    ):
        raise IntensityCalibrationError("强度校准系数必须全部为有限正数")
    if len(item.source_sha256) != 64:
        raise IntensityCalibrationError("强度校准源文件 SHA-256 无效")
    if not item.calibration_id:
        raise IntensityCalibrationError("强度校准记录 ID 不能为空")


def parse_intensity_calibration_bytes(
    content: bytes,
    *,
    device_serial: str,
    pixel_count: int,
    source_name: str,
) -> IntensityCalibration:
    pixel_count = int(pixel_count)
    if pixel_count <= 0:
        raise IntensityCalibrationError("设备有效像素数必须大于 0")
    text = _decode(bytes(content))
    rows = []
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rows.append((line_number, _tokens(line)))
        except IntensityCalibrationError as exc:
            raise IntensityCalibrationError(f"第 {line_number} 行：{exc}") from exc
    if not rows:
        raise IntensityCalibrationError("强度校准文件没有数据")

    if not _is_numeric_row(rows[0][1]):
        rows.pop(0)
    if not rows:
        raise IntensityCalibrationError("强度校准文件只有标题，没有系数")

    column_count = len(rows[0][1])
    coefficients = []
    for expected_pixel, (line_number, values) in enumerate(rows):
        if len(values) != column_count:
            raise IntensityCalibrationError(
                f"第 {line_number} 行列数与前面的数据不一致"
            )
        if column_count == 2:
            try:
                pixel = int(values[0])
            except ValueError as exc:
                raise IntensityCalibrationError(
                    f"第 {line_number} 行 Pixel 无法解析为整数"
                ) from exc
            if pixel != expected_pixel:
                raise IntensityCalibrationError(
                    "Pixel 必须从 0 开始连续递增；"
                    f"第 {line_number} 行期望 {expected_pixel}，实际 {pixel}"
                )
        try:
            coefficient = float(values[-1])
        except ValueError as exc:
            raise IntensityCalibrationError(
                f"第 {line_number} 行数值无法解析"
            ) from exc
        if not math.isfinite(coefficient) or coefficient <= 0:
            raise IntensityCalibrationError(
                f"第 {line_number} 行系数必须是有限正数"
            )
        coefficients.append(coefficient)

    if len(coefficients) != pixel_count:
        raise IntensityCalibrationError(
            f"强度校准系数数量 {len(coefficients)} "
            f"与有效像素数 {pixel_count} 不一致"
        )

    source_sha256 = hashlib.sha256(bytes(content)).hexdigest()
    values = tuple(coefficients)
    item = IntensityCalibration(
        calibration_id=_record_id(
            device_serial, pixel_count, values, source_sha256
        ),
        device_serial=str(device_serial).strip(),
        pixel_count=pixel_count,
        coefficients=values,
        imported_at=datetime.now(timezone.utc).isoformat(),
        source_name=Path(str(source_name)).name,
        source_sha256=source_sha256,
    )
    validate_intensity_calibration(item)
    return item
