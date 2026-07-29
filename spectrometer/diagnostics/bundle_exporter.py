"""Create a verified diagnostic ZIP from an immutable session snapshot."""

from __future__ import annotations

from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import sys
import zipfile

import numpy as np

from .models import FORMAT_VERSION, json_safe


MAX_STARTUP_LOG_BYTES = 256 * 1024
SENSITIVE_LOG_VALUE = re.compile(
    rb"(?i)(password|passwd|token|secret|api[_-]?key)\s*[:=]\s*([^\s]+)"
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_prefix(path: Path) -> bytes:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            return handle.read(size)
    except OSError:
        return b""


def _filter_jsonl(data: bytes, acquisition_id: str) -> bytes:
    if not acquisition_id:
        return data
    output = []
    for raw in data.splitlines():
        try:
            item = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        if item.get("acquisition_id", "") in ("", acquisition_id):
            output.append(raw)
    return b"\n".join(output) + (b"\n" if output else b"")


def _diagnostic_text(timeline: bytes) -> bytes:
    lines = []
    for raw in timeline.splitlines():
        try:
            item = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        payload = item.get("payload", {})
        message = payload.get("message") if isinstance(payload, dict) else None
        event = item.get("event", item.get("kind", "event"))
        lines.append(
            f"{item.get('local_time', '')} [{item.get('level', 'INFO')}] "
            f"{event}: {message or json.dumps(payload, ensure_ascii=False)}"
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


def _redact_log(data: bytes) -> bytes:
    return SENSITIVE_LOG_VALUE.sub(rb"\1=<redacted>", data)


def _frame_files(frames) -> tuple[bytes, bytes]:
    frames = list(frames or [])[-10:]
    arrays = [
        np.frombuffer(item["pixel_bytes"], dtype=">u2").astype(">u2", copy=False)
        for item in frames
    ]
    width = max((array.size for array in arrays), default=0)
    matrix = np.zeros((len(arrays), width), dtype=">u2")
    for row, array in enumerate(arrays):
        matrix[row, : array.size] = array
    buffer = io.BytesIO()
    np.save(buffer, matrix, allow_pickle=False)
    metadata = [
        {key: json_safe(value) for key, value in item.items() if key != "pixel_bytes"}
        for item in frames
    ]
    return buffer.getvalue(), json.dumps(
        {"frames": metadata}, ensure_ascii=False, indent=2
    ).encode("utf-8")


def export_diagnostic_bundle(
    run_dir: Path,
    target: Path,
    *,
    acquisition_id: str = "",
    include_recent_frames: bool = False,
    recent_frames=None,
    startup_log: Path | None = None,
    live_snapshot: bool = False,
    device_snapshot=None,
    acquisition_summary=None,
) -> Path:
    run_dir = Path(run_dir)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix.lower() != ".zip":
        target = target.with_suffix(".zip")
    temporary = target.with_suffix(target.suffix + ".tmp")

    timeline = _filter_jsonl(
        _read_prefix(run_dir / "timeline.jsonl"), acquisition_id
    )
    performance = _filter_jsonl(
        _read_prefix(run_dir / "performance.jsonl"), acquisition_id
    )
    summaries = _filter_jsonl(
        _read_prefix(run_dir / "frame-summaries.jsonl"), acquisition_id
    )
    members = {
        "system.json": json.dumps(
            {
                "platform": platform.platform(),
                "machine": platform.machine(),
                "python": sys.version,
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8"),
        "device.json": json.dumps(
            json_safe(device_snapshot or {}), ensure_ascii=False, indent=2
        ).encode("utf-8"),
        "acquisition-summary.json": json.dumps(
            json_safe(acquisition_summary or {}), ensure_ascii=False, indent=2
        ).encode("utf-8"),
        "timeline.jsonl": timeline,
        "performance.jsonl": performance,
        "frame-summaries.jsonl": summaries,
        "diagnostics.txt": _diagnostic_text(timeline),
    }
    if startup_log is not None:
        log = _read_prefix(Path(startup_log))
        members["startup-log-tail.txt"] = _redact_log(
            log[-MAX_STARTUP_LOG_BYTES:]
        )

    sample_status = "not_requested"
    if include_recent_frames:
        if recent_frames:
            frame_data, metadata = _frame_files(recent_frames)
            members["recent-frames/frames.npy"] = frame_data
            members["recent-frames/metadata.json"] = metadata
            sample_status = "included"
        else:
            sample_status = "unavailable"

    manifest = {
        "format_version": FORMAT_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "run_id": run_dir.name,
        "acquisition_id": acquisition_id,
        "live_snapshot": bool(live_snapshot),
        "recent_frames": sample_status,
        "members": {
            name: {"size": len(data), "sha256": _sha256(data)}
            for name, data in members.items()
        },
    }
    members["bundle-manifest.json"] = json.dumps(
        manifest, ensure_ascii=False, indent=2
    ).encode("utf-8")

    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            for name, data in members.items():
                archive.writestr(name, data)
        with zipfile.ZipFile(temporary, "r") as archive:
            if archive.testzip() is not None:
                raise OSError("诊断包 ZIP 校验失败")
            for name, expected in manifest["members"].items():
                data = archive.read(name)
                if len(data) != expected["size"] or _sha256(data) != expected["sha256"]:
                    raise OSError(f"诊断包成员校验失败：{name}")
        os.replace(temporary, target)
        return target
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
