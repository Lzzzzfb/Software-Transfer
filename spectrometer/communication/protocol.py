"""
光谱仪通信协议模块 — 严格遵循文档中的二进制协议规范。

协议格式:
  发送/返回:
    指令头 (1B): 0x24
    长度字 (2B): U16 LE, 命令码+参数/数据 的字节数
    命令码 (1B)
    参数/数据 (变长)
    校验码 (1B): 长度字/命令码/参数/数据域所有字节的累加和

异常约定: MCU返回的命令码中, bit7=1 表示失败(0x7x), bit7=0 表示成功(0x6x)
"""

import struct
from enum import IntEnum
from typing import Tuple, Optional, List


class CmdCode(IntEnum):
    """命令码枚举"""
    # 通用接口
    GET_VERSION      = 0x01  # 查询控制器版本信息
    GET_PROD_INFO    = 0x02  # 生产信息查询
    GET_TRIG_STATUS  = 0x03  # 查询外触发状态
    DEVICE_INIT      = 0x10  # 设备初始化

    # 参数配置接口
    SET_INTEG_TIME   = 0x20  # 积分时间设置
    SET_TRIG_MODE    = 0x21  # 触发模式设置
    SET_INTERVAL     = 0x22  # 采集间隔时间设置
    SET_AVG_COUNT    = 0x23  # 平均次数设置
    SET_DARK_CURRENT = 0x24  # 去除暗电流设置
    SET_GAIN         = 0x25  # 增益设置
    SET_BAUD_RATE    = 0x26  # 串口波特率设置(TTL)
    SET_CALIB_COEFF  = 0x27  # 矫正系数设置(F32×4)
    SET_SMOOTH_COEFF = 0x28  # 平滑系数设置
    SET_CORR_PARAMS  = 0x29  # 校正参数设置(最多4096个)
    SET_OTHER_COEFF  = 0x2a  # 其他系数设置
    SET_DELAY        = 0x2b  # 延时时间设置
    SET_SERIAL_NUM   = 0x2c  # 产品序列号设置

    # 参数查询接口
    QUERY_INTEG_TIME   = 0x30  # 积分时间查询
    QUERY_TRIG_MODE    = 0x31  # 触发模式查询
    QUERY_INTERVAL     = 0x32  # 采集间隔时间查询
    QUERY_AVG_COUNT    = 0x33  # 平均次数查询
    QUERY_DARK_CURRENT = 0x34  # 去除暗电流查询
    QUERY_GAIN         = 0x35  # 增益查询
    QUERY_BAUD_RATE    = 0x36  # 串口波特率查询
    QUERY_CALIB_COEFF  = 0x37  # 矫正系数查询
    QUERY_SMOOTH_COEFF = 0x38  # 平滑系数查询
    QUERY_CORR_PARAMS  = 0x39  # 校正参数查询
    QUERY_OTHER_COEFF  = 0x3a  # 其他系数查询
    QUERY_DELAY        = 0x3b  # 延时时间查询
    QUERY_SERIAL_NUM   = 0x3c  # 产品序列号查询

    # 控制接口
    START_SINGLE      = 0x50  # 采集启动-单次
    START_CONTINUOUS  = 0x51  # 采集启动-连续
    STOP_ACQUISITION  = 0x52  # 采集停止
    OUTPUT_TRIG       = 0x54  # 输出触发信号(DO0)

    # 数据上传
    DATA_TRANSMIT     = 0x80  # 数据包命令码


class TriggerMode(IntEnum):
    """触发模式 (遵循CCD_CMOS函数表 20251027)"""
    SOFTWARE     = 0  # 软件触发
    SOFT_MASTER  = 1  # 软件触发主机（硬同步用）
    EXTERNAL     = 2  # 外部触发


HEADER_BYTE = 0x24


def calc_checksum(data: bytes) -> int:
    """计算累加和校验码（取低8位）"""
    return sum(data) & 0xFF


def build_packet(cmd: int, params: bytes = b'') -> bytes:
    """
    构建发送数据包。

    Args:
        cmd: 命令码
        params: 参数/数据域（不含命令码）

    Returns:
        完整的数据包字节
    """
    payload = struct.pack('<B', cmd) + params
    length = len(payload)
    length_bytes = struct.pack('<H', length)
    checksum = calc_checksum(length_bytes + payload)
    return bytes([HEADER_BYTE]) + length_bytes + payload + bytes([checksum])


def check_status(status_byte: int) -> int:
    """
    检查响应状态字节。
    协议约定: 0x6x = 成功(ACK), 0x7x = 失败(NAK), 通过bit4区分。
    """
    return 1 if (status_byte & 0x10) else 0


def parse_packet(data: bytes, skip_checksum: bool = False) -> Tuple[int, bytes]:
    """
    解析返回数据包。

    返回 (cmd, params_raw) 其中 params_raw 包含响应中的所有数据字节。
    不自动提取状态位 — 由调用方根据命令码决定如何解释。

    skip_checksum: 为 True 时跳过校验码验证（数据包 cmd=0x80 校验码任意值，无需验证）

    Raises:
        ValueError: 数据包格式错误或校验失败
    """
    if len(data) < 5:
        raise ValueError(f"数据包过短: {len(data)} 字节")
    if data[0] != HEADER_BYTE:
        raise ValueError(f"指令头错误: 期望 0x{HEADER_BYTE:02X}, 收到 0x{data[0]:02X}")

    length = struct.unpack('<H', data[1:3])[0]
    cmd = data[3]
    params = data[4:4 + length - 1]  # length包含cmd, 减去cmd即为参数/数据域

    if not skip_checksum:
        checksum = data[4 + length - 1]
        # 校验范围: length_bytes + cmd + params (不含checksum自身)
        payload_for_check = data[1:4 + length - 1]
        expected = calc_checksum(payload_for_check)
        if checksum != expected:
            raise ValueError(f"校验码错误: 期望 0x{expected:02X}, 收到 0x{checksum:02X}")

    return cmd, params


# struct 格式缓存: {n_pixel: fmt_string}
_UNPACK_FMT_CACHE: dict = {}

def _get_unpack_fmt(n_pixels: int) -> str:
    """缓存并返回 '>HH...H' 格式串, 避免每帧重复构造"""
    if n_pixels not in _UNPACK_FMT_CACHE:
        _UNPACK_FMT_CACHE[n_pixels] = '>' + 'H' * n_pixels
    return _UNPACK_FMT_CACHE[n_pixels]


def parse_data_packet(data: bytes,
                      n_pixel: int = 0,
                      n_start_pixel: int = 0,
                      n_valid_pixel: int = 0) -> dict:
    """
    解析数据采集包 (cmd=0x80) — 使用 memoryview 避免拷贝。
    """
    if data[0] != HEADER_BYTE:
        raise ValueError("指令头错误")
    length = struct.unpack('<H', data[1:3])[0]
    cmd = data[3]
    if cmd != CmdCode.DATA_TRANSMIT:
        raise ValueError(f"非数据包命令码: 0x{cmd:02X}")

    # memoryview 零拷贝访问数据区
    mv = memoryview(data)[4:4 + length - 1]

    # 按 nPixel 截取有效负载 (仅计算偏移, 不复制)
    if n_pixel > 0:
        mv = mv[:2 + 2 * n_pixel]

    if len(mv) < 2:
        return {'packet_number': 0, 'pixels': [], 'pixel_count': 0}

    # 包序号 (U16 小端)
    packet_number = struct.unpack('<H', mv[:2])[0]

    # 像素 — 一次 struct.unpack C 调用, 格式串缓存
    pixel_bytes = mv[2:]
    n_pairs = len(pixel_bytes) // 2
    raw_pixels = struct.unpack(_get_unpack_fmt(n_pairs), pixel_bytes[:n_pairs * 2])

    # 裁剪
    if n_start_pixel > 0 or n_valid_pixel > 0:
        start = max(0, n_start_pixel)
        end = start + n_valid_pixel if n_valid_pixel > 0 else len(raw_pixels)
        start = min(start, len(raw_pixels))
        end = min(end, len(raw_pixels))
        pixels = list(raw_pixels[start:end])
    else:
        # 直接转 list, 不额外分配中间变量
        pixels = list(raw_pixels)

    return {
        'packet_number': packet_number,
        'pixels': pixels,
        'pixel_count': len(pixels),
    }


def build_set_integ_time(time_us: int) -> bytes:
    """构建积分时间设置指令"""
    return build_packet(CmdCode.SET_INTEG_TIME, struct.pack('<I', time_us))


def build_set_trig_mode(mode: int) -> bytes:
    """构建触发模式设置指令"""
    return build_packet(CmdCode.SET_TRIG_MODE, struct.pack('<B', mode))


def build_set_interval(interval_us: int) -> bytes:
    """构建采集间隔设置指令"""
    return build_packet(CmdCode.SET_INTERVAL, struct.pack('<I', interval_us))


def build_set_avg_count(count: int) -> bytes:
    """构建平均次数设置指令"""
    return build_packet(CmdCode.SET_AVG_COUNT, struct.pack('<I', count))


def build_set_dark_current(enable: bool) -> bytes:
    """构建暗电流去除设置指令"""
    return build_packet(CmdCode.SET_DARK_CURRENT, struct.pack('<B', 1 if enable else 0))


def build_set_gain(gain: int) -> bytes:
    """构建增益设置指令"""
    return build_packet(CmdCode.SET_GAIN, struct.pack('<B', gain))


def build_set_calib_coeff(c1: float, c2: float, c3: float, c4: float) -> bytes:
    """构建矫正系数设置指令"""
    return build_packet(CmdCode.SET_CALIB_COEFF, struct.pack('<4f', c1, c2, c3, c4))


def build_set_delay(delay_us: int) -> bytes:
    """构建延时时间设置指令"""
    return build_packet(CmdCode.SET_DELAY, struct.pack('<I', delay_us))


def build_start_single() -> bytes:
    """构建单次采集启动指令"""
    return build_packet(CmdCode.START_SINGLE)


def build_start_continuous() -> bytes:
    """构建连续采集启动指令"""
    return build_packet(CmdCode.START_CONTINUOUS)


def build_stop() -> bytes:
    """构建停止采集指令"""
    return build_packet(CmdCode.STOP_ACQUISITION)


def build_query(cmd: int) -> bytes:
    """构建通用查询指令"""
    return build_packet(cmd)
