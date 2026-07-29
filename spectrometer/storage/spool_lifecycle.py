"""采集临时缓存的安全清理规则。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional, Tuple


@dataclass(frozen=True)
class SpoolCleanupResult:
    removed: Tuple[Path, ...]
    retained: Tuple[Path, ...]
    warnings: Tuple[str, ...]


def cleanup_spool_files(
    paths: Iterable,
    *,
    unlink: Optional[Callable[[Path], None]] = None,
) -> SpoolCleanupResult:
    """删除去重后的缓存文件，并把失败路径留给恢复流程。"""

    remover = unlink or (lambda path: path.unlink(missing_ok=True))
    unique_paths = tuple(dict.fromkeys(Path(path) for path in paths if path))
    removed = []
    retained = []
    warnings = []
    for path in unique_paths:
        try:
            remover(path)
        except OSError as exc:
            retained.append(path)
            warnings.append(f"临时采集缓存清理失败，已保留 {path}：{exc}")
        else:
            removed.append(path)
    return SpoolCleanupResult(
        removed=tuple(removed),
        retained=tuple(retained),
        warnings=tuple(warnings),
    )
