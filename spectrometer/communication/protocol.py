"""光谱仪二进制通信协议。

普通帧的 ``nLength`` 不包含校验码，物理总长度为 ``4 + nLength``::

    0x24 | nLength(U16LE) | Cmd | Params | Checksum

采集数据帧（Cmd=0x80）是下位机协议中的特例：``nLength`` 包含校验码，
物理总长度为 ``3 + nLength``::

    0x24 | nLength(U16LE) | 0x80 | nPacketNumb(U32LE)
         | Pixels(U16BE...) | Checksum

其中 ``nLength = 6 + 2 * pixel_count``。数据帧中的校验字节会被消费，
但根据现有下位机约定不进行数值校验。
"""

from enum import IntEnum
import struct
from typing import Dict, List, Tuple


class CmdCode(IntEnum):
    """命令码。"""

    GET_VERSION = 0x01
    GET_PROD_INFO = 0x02
    GET_TRIG_STATUS = 0x03
    DEVICE_INIT = 0x10

    SET_INTEG_TIME = 0x20
    SET_TRIG_MODE = 0x21
    SET_INTERVAL = 0x22
    SET_AVG_COUNT = 0x23
    SET_DARK_CURRENT = 0x24
    SET_GAIN = 0x25
    SET_BAUD_RATE = 0x26
    SET_CALIB_COEFF = 0x27
    SET_SMOOTH_COEFF = 0x28
    SET_CORR_PARAMS = 0x29
    SET_OTHER_COEFF = 0x2A
    SET_DELAY = 0x2B
    SET_SERIAL_NUM = 0x2C

    QUERY_INTEG_TIME = 0x30
    QUERY_TRIG_MODE = 0x31
    QUERY_INTERVAL = 0x32
    QUERY_AVG_COUNT = 0x33
    QUERY_DARK_CURRENT = 0x34
    QUERY_GAIN = 0x35
    QUERY_BAUD_RATE = 0x36
    QUERY_CALIB_COEFF = 0x37
    QUERY_SMOOTH_COEFF = 0x38
    QUERY_CORR_PARAMS = 0x39
    QUERY_OTHER_COEFF = 0x3A
    QUERY_DELAY = 0x3B
    QUERY_SERIAL_NUM = 0x3C

    START_SINGLE = 0x50
    START_CONTINUOUS = 0x51
    STOP_ACQUISITION = 0x52
    OUTPUT_TRIG = 0x54

    DATA_TRANSMIT = 0x80


class TriggerMode(IntEnum):
    """触发模式（CCD/CMOS 函数表 2025-10-27）。"""

    SOFTWARE = 0
    SOFT_MASTER = 1
    EXTERNAL = 2


HEADER_BYTE = 0x24
DATA_FRAME_FIXED_LENGTH = 6  # Cmd(1) + nPacketNumb(4) + Checksum(1)


def calc_checksum(data: bytes) -> int:
    """计算累加和校验码（低 8 位）。"""

    return sum(data) & 0xFF


def frame_total_length(length: int, cmd: int) -> int:
    """根据长度字段和命令码返回帧的物理总字节数。"""

    if cmd == CmdCode.DATA_TRANSMIT:
        if length < DATA_FRAME_FIXED_LENGTH:
            raise ValueError(f"0x80 数据帧长度非法: {length}，最小为 6")
        if (length - DATA_FRAME_FIXED_LENGTH) % 2:
            raise ValueError(f"0x80 数据帧像素区不是偶数字节: nLength={length}")
        return 3 + length

    if length < 1:
        raise ValueError(f"普通帧长度非法: {length}，至少应包含 Cmd")
    return 4 + length


def inspect_frame_header(data: bytes) -> Tuple[int, int, int]:
    """读取帧头，返回 ``(nLength, cmd, total_length)``。"""

    if len(data) < 4:
        raise ValueError(f"帧头不完整: {len(data)} 字节")
    if data[0] != HEADER_BYTE:
        raise ValueError(
            f"帧头错误: 期望 0x{HEADER_BYTE:02X}，收到 0x{data[0]:02X}"
        )
    length = struct.unpack_from("<H", data, 1)[0]
    cmd = data[3]
    return length, cmd, frame_total_length(length, cmd)


def _require_complete_frame(data: bytes) -> Tuple[int, int, int]:
    length, cmd, total_length = inspect_frame_header(data)
    if len(data) != total_length:
        raise ValueError(
            f"帧长度不匹配: Cmd=0x{cmd:02X}，期望 {total_length} 字节，"
            f"实际 {len(data)} 字节"
        )
    return length, cmd, total_length


def build_packet(cmd: int, params: bytes = b"") -> bytes:
    """构建上位机发送的普通命令帧。"""

    payload = struct.pack("<B", cmd) + params
    length_bytes = struct.pack("<H", len(payload))
    checksum = calc_checksum(length_bytes + payload)
    return bytes([HEADER_BYTE]) + length_bytes + payload + bytes([checksum])


def check_status(status_byte: int) -> int:
    """按旧接口返回响应状态：0 表示成功，1 表示失败。"""

    return 1 if (status_byte & 0x10) else 0


def parse_packet(data: bytes, skip_checksum: bool = False) -> Tuple[int, bytes]:
    """解析完整帧，返回 ``(cmd, params)``。

    普通帧默认验证校验码；``skip_checksum`` 仅保留给兼容调用方。
    0x80 数据帧的末尾校验字节始终只消费、不验证。
    """

    length, cmd, total_length = _require_complete_frame(data)

    if cmd == CmdCode.DATA_TRANSMIT:
        return cmd, data[4 : total_length - 1]

    params_end = 3 + length
    params = data[4:params_end]
    checksum = data[params_end]
    if not skip_checksum:
        expected = calc_checksum(data[1:params_end])
        if checksum != expected:
            raise ValueError(
                f"校验码错误: 期望 0x{expected:02X}，收到 0x{checksum:02X}"
            )
    return cmd, params


_UNPACK_FMT_CACHE: Dict[int, str] = {}


def _get_unpack_fmt(n_pixels: int) -> str:
    if n_pixels not in _UNPACK_FMT_CACHE:
        _UNPACK_FMT_CACHE[n_pixels] = ">" + "H" * n_pixels
    return _UNPACK_FMT_CACHE[n_pixels]


def parse_data_packet(
    data: bytes,
    n_pixel: int = 0,
    n_start_pixel: int = 0,
    n_valid_pixel: int = 0,
) -> dict:
    """解析完整的 0x80 采集帧。

    ``nPacketNumb`` 按 U32 小端读取，其中高 24 位是帧序号、低 8 位是
    保留位。像素按 U16 大端读取。若设备已报告 ``n_pixel``，数据帧中的
    原始像素数必须与之相同，以便尽早暴露串口错帧或设备配置不一致。
    """

    length, cmd, total_length = _require_complete_frame(data)
    if cmd != CmdCode.DATA_TRANSMIT:
        raise ValueError(f"非数据帧命令码: 0x{cmd:02X}")

    packet_number = struct.unpack_from("<I", data, 4)[0]
    pixel_view = memoryview(data)[8 : total_length - 1]
    source_pixel_count = len(pixel_view) // 2

    expected_from_length = (length - DATA_FRAME_FIXED_LENGTH) // 2
    if source_pixel_count != expected_from_length:
        raise ValueError(
            f"像素数量与长度字段不匹配: {source_pixel_count} != {expected_from_length}"
        )
    if n_pixel > 0 and source_pixel_count != n_pixel:
        raise ValueError(
            f"像素数量与设备信息不一致: 数据帧 {source_pixel_count}，设备 {n_pixel}"
        )

    if source_pixel_count:
        raw_pixels = struct.unpack(
            _get_unpack_fmt(source_pixel_count), pixel_view
        )
    else:
        raw_pixels = ()

    start = min(max(0, n_start_pixel), source_pixel_count)
    if n_valid_pixel > 0:
        end = min(start + n_valid_pixel, source_pixel_count)
    else:
        end = source_pixel_count
    pixels: List[int] = list(raw_pixels[start:end])

    return {
        "packet_number": packet_number,
        "frame_sequence": (packet_number >> 8) & 0xFFFFFF,
        "reserved": packet_number & 0xFF,
        "pixels": pixels,
        "pixel_count": len(pixels),
        "source_pixel_count": source_pixel_count,
        "checksum": data[total_length - 1],
    }


def build_set_integ_time(time_us: int) -> bytes:
    return build_packet(CmdCode.SET_INTEG_TIME, struct.pack("<I", time_us))


def build_set_trig_mode(mode: int) -> bytes:
    return build_packet(CmdCode.SET_TRIG_MODE, struct.pack("<B", mode))


def build_set_interval(interval_us: int) -> bytes:
    return build_packet(CmdCode.SET_INTERVAL, struct.pack("<I", interval_us))


def build_set_avg_count(count: int) -> bytes:
    return build_packet(CmdCode.SET_AVG_COUNT, struct.pack("<I", count))


def build_set_dark_current(enable: bool) -> bytes:
    return build_packet(CmdCode.SET_DARK_CURRENT, struct.pack("<B", int(enable)))


def build_set_gain(gain: int) -> bytes:
    return build_packet(CmdCode.SET_GAIN, struct.pack("<B", gain))


def build_set_calib_coeff(c1: float, c2: float, c3: float, c4: float) -> bytes:
    return build_packet(CmdCode.SET_CALIB_COEFF, struct.pack("<4f", c1, c2, c3, c4))


def build_set_delay(delay_us: int) -> bytes:
    return build_packet(CmdCode.SET_DELAY, struct.pack("<I", delay_us))


def build_start_single() -> bytes:
    return build_packet(CmdCode.START_SINGLE)


def build_start_continuous() -> bytes:
    return build_packet(CmdCode.START_CONTINUOUS)


def build_stop() -> bytes:
    return build_packet(CmdCode.STOP_ACQUISITION)


def build_query(cmd: int) -> bytes:
    return build_packet(cmd)
