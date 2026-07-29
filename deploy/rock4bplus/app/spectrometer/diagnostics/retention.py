"""Safe age and size retention for diagnostic sessions."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import time


def _session(path: Path):
    if path.is_symlink() or not path.is_dir() or not path.name.startswith("run-"):
        return None
    manifest = path / "manifest.json"
    try:
        values = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if values.get("run_id") != path.name or values.get("status") == "active":
        return None
    try:
        resolved = path.resolve()
        root = path.parent.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    files = [item for item in resolved.rglob("*") if item.is_file()]
    size = sum(item.stat().st_size for item in files)
    modified = manifest.stat().st_mtime
    return resolved, modified, size


def enforce_retention(
    root: Path, *, max_age_days: int = 7, max_bytes: int = 200 * 1024 * 1024
) -> list[Path]:
    root = Path(root)
    if not root.exists():
        return []
    sessions = [item for item in (_session(path) for path in root.iterdir()) if item]
    cutoff = time.time() - max_age_days * 86400
    removed = []
    survivors = []
    for path, modified, size in sessions:
        if modified < cutoff:
            shutil.rmtree(path)
            removed.append(path)
        else:
            survivors.append((path, modified, size))
    total = sum(item[2] for item in survivors)
    for path, _, size in sorted(survivors, key=lambda item: item[1]):
        if total <= max_bytes:
            break
        shutil.rmtree(path)
        removed.append(path)
        total -= size
    return removed
