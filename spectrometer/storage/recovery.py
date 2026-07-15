"""扫描和恢复上次未正常结束的采集缓存。"""

from pathlib import Path
from typing import Dict

from .spool import SpoolRecovery, read_spool


def scan_pending(directory) -> Dict[Path, SpoolRecovery]:
    results = {}
    for path in sorted(Path(directory).glob("*.part")):
        try:
            results[path] = read_spool(path)
        except (OSError, ValueError):
            continue
    return results
