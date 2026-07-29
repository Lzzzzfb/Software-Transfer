"""Safe paths for diagnostic run sessions."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import uuid

from ..services.platform_paths import diagnostic_directory


SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def new_run_id() -> str:
    return f"run-{datetime.now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"


def validate_id(value: str) -> str:
    value = str(value)
    if not SAFE_ID.fullmatch(value):
        raise ValueError(f"不安全的诊断会话标识：{value!r}")
    return value


def run_directory(run_id: str, root: Path | None = None) -> Path:
    base = Path(root) if root is not None else diagnostic_directory()
    return base / validate_id(run_id)


def latest_run_with_acquisition(root: Path, *, exclude: Path | None = None):
    candidates = []
    for path in Path(root).glob("run-*"):
        if exclude is not None and path == exclude:
            continue
        try:
            manifest = json.loads(
                (path / "manifest.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            continue
        acquisition_id = manifest.get("latest_acquisition_id", "")
        if acquisition_id and manifest.get("run_id") == path.name:
            candidates.append(
                ((path / "manifest.json").stat().st_mtime, path, acquisition_id)
            )
    if not candidates:
        return None
    _, path, acquisition_id = max(candidates, key=lambda item: item[0])
    return path, acquisition_id
