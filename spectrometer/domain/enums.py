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
