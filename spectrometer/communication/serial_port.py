"""
串口通信模块 — 每个设备独立 QThread 运行, 避免主线程阻塞。
"""

import struct
from typing import Optional, List

from PyQt5.QtCore import (
    QObject, pyqtSignal, pyqtSlot, QThread, QMetaObject, Qt, Q_ARG
)
from PyQt5.QtSerialPort import QSerialPort, QSerialPortInfo

from .protocol import (
    HEADER_BYTE, CmdCode,
    build_packet, parse_packet, parse_data_packet,
)

MAX_BUFFER_SIZE = 2 * 1024 * 1024  # 缓冲区上限 2MB, 防止异常数据导致内存溢出


class SerialWorker(QObject):
    """串口通信工作对象 — 运行在独立 QThread 中,
       所有串口 I/O 操作不阻塞主线程。"""

    # 跨线程信号 (Qt 自动处理线程安全)
    data_received   = pyqtSignal(int, list)          # (device_id, pixels)
    packet_error    = pyqtSignal(int, str)            # (device_id, error_msg)
    connection_lost = pyqtSignal(int)                 # (device_id)
    response_ready  = pyqtSignal(int, int, bytes)     # (device_id, cmd, params)
    open_finished   = pyqtSignal(int, bool, str)      # (device_id, success, error_msg)

    def __init__(self, device_index: int, port_name: str, baud_rate: int):
        super().__init__()
        self.device_index = device_index
        self._port_name = port_name
        self._baud_rate = baud_rate
        self._serial: Optional[QSerialPort] = None
        self._buffer = bytearray()
        # 像素信息 (由设备管理器在收到版本信息后设置)
        self._n_pixel = 0
        self._n_start_pixel = 0
        self._n_valid_pixel = 0
        # 最新解析出的像素 — worker 线程写入, 主线程通过 DeviceManager 轮询读取
        self.latest_pixels = None

    @pyqtSlot()
    def do_connect(self):
        """在线程中打开串口 (由 thread.started 触发)"""
        try:
            self._serial = QSerialPort()
            self._serial.setPortName(self._port_name)
            self._serial.setBaudRate(self._baud_rate)
            self._serial.setDataBits(QSerialPort.Data8)
            self._serial.setParity(QSerialPort.NoParity)
            self._serial.setStopBits(QSerialPort.OneStop)
            self._serial.setFlowControl(QSerialPort.NoFlowControl)

            if self._serial.open(QSerialPort.ReadWrite):
                self._serial.readyRead.connect(self._on_ready_read)
                self._serial.errorOccurred.connect(self._on_error)
                self.open_finished.emit(self.device_index, True, '')
            else:
                err = self._serial.errorString()
                self.open_finished.emit(self.device_index, False, err)
        except Exception as e:
            self.open_finished.emit(self.device_index, False, str(e))

    @pyqtSlot()
    def do_close(self):
        """在线程中关闭串口"""
        if self._serial and self._serial.isOpen():
            self._serial.close()
        self._buffer.clear()

    @pyqtSlot(bytes)
    def do_write(self, data: bytes):
        """在线程中写入数据 (由主线程通过 invokeMethod 调用)"""
        if self._serial and self._serial.isOpen():
            self._serial.write(data)

    @pyqtSlot(int, bytes)
    def do_send_command(self, cmd: int, params: bytes):
        """在线程中构建并发送协议包"""
        packet = build_packet(cmd, params)
        self.do_write(packet)

    @pyqtSlot(int, int, int)
    def set_pixel_info(self, n_pixel: int, n_start_pixel: int, n_valid_pixel: int):
        """由主线程在收到版本信息后调用, 设置像素参数用于数据包解析"""
        self._n_pixel = n_pixel
        self._n_start_pixel = n_start_pixel
        self._n_valid_pixel = n_valid_pixel

    # ==================== 内部方法 (运行在 Worker 线程) ====================

    def _on_ready_read(self):
        """串口数据到达 — 运行在 Worker 线程"""
        data = bytes(self._serial.readAll())
        self._buffer.extend(data)

        # 缓冲区溢出保护
        if len(self._buffer) > MAX_BUFFER_SIZE:
            self._buffer = self._buffer[-MAX_BUFFER_SIZE // 2:]
            self.packet_error.emit(self.device_index, '缓冲区溢出, 已截断')

        self._parse_buffer()

    def _parse_buffer(self):
        """解析缓冲区中的完整数据包"""
        while len(self._buffer) >= 5:
            # 帧同步: 查找帧头 0x24
            if self._buffer[0] != HEADER_BYTE:
                idx = self._buffer.find(HEADER_BYTE)
                if idx == -1:
                    self._buffer.clear()
                    return
                self._buffer = self._buffer[idx:]

            length = struct.unpack('<H', self._buffer[1:3])[0]

            # 长度合理性检查: 光谱数据通常 < 64KB
            if length == 0 or length > 65535:
                # 可能是帧同步丢失, 跳过当前字节重试
                self._buffer = self._buffer[1:]
                continue

            total_len = 4 + length  # header(1) + length(2) + cmd(1) + params + checksum(1)

            if len(self._buffer) < total_len:
                return  # 等待完整包

            packet_data = bytes(self._buffer[:total_len])
            self._buffer = self._buffer[total_len:]

            try:
                is_data = (packet_data[3] == CmdCode.DATA_TRANSMIT)
                cmd, params = parse_packet(packet_data, skip_checksum=is_data)
                if is_data:
                    parsed = parse_data_packet(
                        packet_data,
                        n_pixel=self._n_pixel,
                        n_start_pixel=self._n_start_pixel,
                        n_valid_pixel=self._n_valid_pixel,
                    )
                    # 无信号 — 直接写属性, 主线程轮询读取
                    self.latest_pixels = parsed['pixels']
                else:
                    self.response_ready.emit(self.device_index, cmd, params)
            except ValueError as e:
                self.packet_error.emit(self.device_index, str(e))

    def _on_error(self, error: QSerialPort.SerialPortError):
        if error == QSerialPort.ResourceError:
            self.connection_lost.emit(self.device_index)


class DeviceFinder(QObject):
    """设备发现 — 扫描可用串口并识别光谱仪"""

    devices_found = pyqtSignal(list)

    @staticmethod
    def list_available_ports() -> list:
        ports = []
        for info in QSerialPortInfo.availablePorts():
            ports.append({
                'port_name': info.portName(),
                'description': info.description(),
                'manufacturer': info.manufacturer(),
                'serial_number': info.serialNumber(),
                'vendor_id': info.vendorIdentifier(),
                'product_id': info.productIdentifier(),
                'system_location': info.systemLocation(),
            })
        return ports

    @staticmethod
    def is_likely_spectrometer(port_info: dict) -> bool:
        known_vids = {0x0483, 0x1A86, 0x10C4, 0x067B, 0x0403}
        vid = port_info.get('vendor_id', 0)
        desc = port_info.get('description', '').lower()
        mfr = port_info.get('manufacturer', '').lower()
        if vid in known_vids:
            return True
        for kw in ['usb serial', 'vcp', 'stmicro', 'ch340', 'cp210',
                    'ftdi', 'spectrometer']:
            if kw in desc or kw in mfr:
                return True
        return False
