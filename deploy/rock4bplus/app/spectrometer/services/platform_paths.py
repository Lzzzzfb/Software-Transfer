"""Linux/XDG 用户目录；运行文件与用户数据保持分离。"""

import os
from pathlib import Path


def config_directory() -> Path:
    root = os.environ.get("XDG_CONFIG_HOME")
    base = Path(root).expanduser() if root else Path.home() / ".config"
    return base / "ZGCAI" / "Spectrometer"


def data_directory() -> Path:
    return Path.home() / "ZGCAI-Spectrometer-Data"


def log_directory() -> Path:
    root = os.environ.get("XDG_STATE_HOME")
    base = Path(root).expanduser() if root else Path.home() / ".local" / "state"
    return base / "ZGCAI" / "Spectrometer"
