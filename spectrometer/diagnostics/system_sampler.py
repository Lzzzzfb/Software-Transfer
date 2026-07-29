"""Linux system and process sampler without external commands."""

from __future__ import annotations

import os
from pathlib import Path
import shutil


def _read_text(path: Path):
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None


def process_sample(pid: int | None) -> dict:
    if not pid:
        return {"pid": pid, "alive": False}
    base = Path("/proc") / str(int(pid))
    stat = _read_text(base / "stat")
    status = _read_text(base / "status")
    if stat is None:
        return {"pid": int(pid), "alive": False}
    close = stat.rfind(")")
    fields = stat[close + 2 :].split()
    values = {
        "pid": int(pid),
        "alive": True,
        "state": fields[0] if fields else None,
        "cpu_ticks": (
            int(fields[11]) + int(fields[12]) if len(fields) > 12 else None
        ),
        "rss_bytes": None,
        "threads": None,
    }
    if status:
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                values["rss_bytes"] = int(line.split()[1]) * 1024
            elif line.startswith("Threads:"):
                values["threads"] = int(line.split()[1])
    return values


def _memory_sample() -> dict:
    text = _read_text(Path("/proc/meminfo"))
    result = {"total_bytes": None, "available_bytes": None}
    if text:
        fields = {}
        for line in text.splitlines():
            key, _, value = line.partition(":")
            if value.strip():
                fields[key] = int(value.split()[0]) * 1024
        result["total_bytes"] = fields.get("MemTotal")
        result["available_bytes"] = fields.get("MemAvailable")
    return result


def _temperature_sample():
    for path in sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp")):
        value = _read_text(path)
        try:
            return {"celsius": int(value) / 1000.0, "source": str(path)}
        except (TypeError, ValueError):
            continue
    return {"celsius": None, "source": None}


def sample_system(*, child_pids=(), paths=()) -> dict:
    try:
        load = list(os.getloadavg())
    except (AttributeError, OSError):
        load = [None, None, None]
    disks = {}
    for path in paths:
        try:
            usage = shutil.disk_usage(Path(path))
            disks[str(path)] = {"free_bytes": usage.free, "total_bytes": usage.total}
        except OSError:
            disks[str(path)] = {"free_bytes": None, "total_bytes": None}
    return {
        "load_average": load,
        "memory": _memory_sample(),
        "temperature": _temperature_sample(),
        "main_process": process_sample(os.getpid()),
        "child_processes": [process_sample(pid) for pid in child_pids],
        "disks": disks,
    }
