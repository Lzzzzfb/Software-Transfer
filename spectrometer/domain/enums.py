from enum import Enum


class DeviceState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    INITIALIZING = "initializing"
    READY = "ready"
    ARMED = "armed"
    ACQUIRING = "acquiring"
    ERROR = "error"


class AcquisitionMode(str, Enum):
    SINGLE = "single"
    CONTINUOUS = "continuous"


class AcquisitionOwner(str, Enum):
    GLOBAL = "global"
    LOCAL = "local"
    CALIBRATION = "calibration"
    SCAN = "scan"


class ControlState(str, Enum):
    IDLE = "idle"
    CONFIGURING = "configuring"
    STARTING = "starting"
    ACQUIRING = "acquiring"
    STOPPING = "stopping"
    FINALIZING = "finalizing"
    ERROR = "error"


_CONTROL_STATE_LABELS = {
    ControlState.IDLE: "空闲",
    ControlState.CONFIGURING: "正在配置",
    ControlState.STARTING: "正在启动",
    ControlState.ACQUIRING: "采集中",
    ControlState.STOPPING: "正在停止",
    ControlState.FINALIZING: "正在保存",
    ControlState.ERROR: "错误",
}


def control_state_label(state: ControlState) -> str:
    return _CONTROL_STATE_LABELS[ControlState(state)]


class SyncMode(str, Enum):
    INDEPENDENT = "independent"
    SOFTWARE = "software"
    HARD_INTERNAL = "hard_internal"
    HARD_EXTERNAL = "hard_external"


class StorageFormat(str, Enum):
    CSV = "csv"
    EXCEL = "excel"
    CSV_EXCEL = "csv_excel"


class ProcessingMode(str, Enum):
    RAW = "raw"
    DARK_SUBTRACT = "dark_subtract"
    ABSORBANCE = "absorbance"
    CUSTOM = "custom"
