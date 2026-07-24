"""Human-readable, collision-safe names for acquisition table files."""

from datetime import datetime
from pathlib import Path
import re
from typing import Dict, Iterable, Tuple

from ..domain.enums import AcquisitionOwner
from ..domain.models import AcquisitionRequest


_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1F]')
_MULTIPLE_UNDERSCORES = re.compile(r"_+")


def sanitize_filename_part(value, fallback="未命名") -> str:
    cleaned = _INVALID_FILENAME.sub("_", str(value)).strip(" .")
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = _MULTIPLE_UNDERSCORES.sub("_", cleaned).strip("_")
    return cleaned or fallback


def device_file_token(device) -> str:
    serial = sanitize_filename_part(device.info.prod_serial, "")
    if serial:
        serial = serial if serial.upper().startswith("SN") else f"SN{serial}"
    else:
        serial = f"设备{device.device_id}"
    return serial


def build_session_file_stems(
    request: AcquisitionRequest, devices: Iterable
) -> Tuple[str, Dict[int, str]]:
    devices = list(devices)
    by_id = {device.device_id: device for device in devices}
    if set(by_id) != set(request.device_ids):
        raise ValueError("文件命名设备集合与采集请求不一致")
    started = datetime.fromisoformat(request.started_at)
    date_token = started.strftime("%Y%m%d")
    device_stems = {
        device_id: f"{date_token}_{device_file_token(by_id[device_id])}"
        for device_id in request.device_ids
    }
    if request.owner is AcquisitionOwner.GLOBAL:
        workbook_stem = f"{date_token}_多设备"
    else:
        workbook_stem = device_stems[request.device_ids[0]]
    return workbook_stem, device_stems


def unique_path(path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = {
        item.name.lower()
        for item in target.parent.iterdir()
        if item.is_file()
    }
    if target.name.lower() not in existing:
        return target
    for counter in range(1, 10_000):
        candidate = target.with_name(
            f"{target.stem}_{counter:02d}{target.suffix}"
        )
        if candidate.name.lower() not in existing:
            return candidate
    raise FileExistsError(f"无法为 {target.name} 生成唯一文件名")
