"""面向最终 CSV/Excel 的中文显示文本转换。

内部元数据键和值保持稳定的英文协议形式，仅在写入用户文件时转换。
未知的扩展字段原样保留，避免丢失第三方或未来版本的元数据。
"""

from enum import Enum


_LABELS = {
    "Device ID": "设备编号",
    "Frame Count": "帧数",
    "Export Time": "导出时间",
    "Port": "串口",
    "Serial": "生产序列号",
    "Integration Time (us)": "积分时间 (μs)",
    "Trigger Mode": "触发模式",
    "Processing Mode": "处理模式",
    "Processing Modes": "处理模式",
    "Background Applied": "已应用背景",
    "Reference Applied": "已应用参考",
    "Intensity Calibration Applied": "已应用强度校准",
    "Intensity Calibration ID": "强度校准 ID",
    "airPLS Applied": "已应用 airPLS",
    "airPLS Lambda": "airPLS λ",
    "airPLS Order": "airPLS 差分阶数",
    "airPLS Max Iterations": "airPLS 最大迭代次数",
    "Processing Pipeline Version": "处理管线版本",
    "Rejected Frame Count": "拒绝帧数",
    "Rejected Frames": "拒绝帧数",
    "Session ID": "会话编号",
    "Batch": "批次",
    "Sync Mode": "同步方式",
    "Source": "数据来源",
    "Recovered From": "恢复来源",
}

_BOOLEAN_KEYS = {
    "Background Applied",
    "Reference Applied",
    "Intensity Calibration Applied",
    "airPLS Applied",
}

_PROCESSING_MODES = {
    "raw": "原始强度",
    "dark_subtract": "扣背景",
    "absorbance": "吸光度",
    "custom": "自定义公式",
}

_TRIGGER_MODES = {
    0: "软件触发",
    1: "软件主机触发",
    2: "外部触发",
    "0": "软件触发",
    "1": "软件主机触发",
    "2": "外部触发",
}

_SYNC_MODES = {
    "independent": "独立采集",
    "software": "软件同步",
    "hard_internal": "内部硬同步",
    "hard_external": "外部硬同步",
}

_SOURCE_VALUES = {
    "sealed acquisition spool": "独立采集进程封存缓存",
}


def _enum_value(value):
    return value.value if isinstance(value, Enum) else value


def localize_metadata_label(key) -> str:
    """返回导出文件中的中文字段名；未知字段名原样返回。"""

    text = str(key)
    return _LABELS.get(text, text)


def localize_metadata_value(key, value):
    """转换已知枚举/布尔显示值；未知值保留原类型和值。"""

    key_text = str(key)
    normalized = _enum_value(value)

    if key_text in _BOOLEAN_KEYS:
        if isinstance(normalized, bool):
            return "是" if normalized else "否"
        if isinstance(normalized, str):
            lowered = normalized.strip().lower()
            if lowered in {"true", "yes"}:
                return "是"
            if lowered in {"false", "no"}:
                return "否"

    if key_text == "Processing Mode":
        return _PROCESSING_MODES.get(normalized, value)

    if key_text == "Processing Modes" and isinstance(normalized, str):
        modes = [part.strip() for part in normalized.split(",")]
        return "、".join(_PROCESSING_MODES.get(mode, mode) for mode in modes)

    if key_text == "Trigger Mode":
        return _TRIGGER_MODES.get(normalized, value)

    if key_text == "Sync Mode":
        return _SYNC_MODES.get(normalized, value)

    if key_text == "Source":
        return _SOURCE_VALUES.get(normalized, value)

    return value
