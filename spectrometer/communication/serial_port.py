"""串口通信：每台设备在独立 QThread 中完成非阻塞 I/O。"""

from typing import Optional
import time

from ..qt import QtCore, QtSerialPort, Signal, Slot
from ..domain.models import SpectrumFrame

from .frame_decoder import FrameDecoder
from .protocol import CmdCode, build_packet, parse_data_packet, parse_packet


MAX_BUFFER_SIZE = 2 * 1024 * 1024
CH569W_VENDOR_ID = 0x1A86
CH569W_PRODUCT_ID = 0xFE0C
FRAME_EMIT_INTERVAL_NS = 50_000_000


QSerialPort = QtSerialPort.QSerialPort
QSerialPortInfo = QtSerialPort.QSerialPortInfo


def _serial_enum(group_name: str, value_name: str):
    """兼容 PySide6 scoped enum 与 PyQt5 flat enum。"""

    if hasattr(QSerialPort, value_name):
        return getattr(QSerialPort, value_name)
    return getattr(getattr(QSerialPort, group_name), value_name)


class SerialWorker(QtCore.QObject):
    """单台光谱仪的串口工作对象。"""

    data_received = Signal(int, list)
    frame_received = Signal(object)
    packet_error = Signal(int, str)
    connection_lost = Signal(int)
    response_ready = Signal(int, int, bytes)
    open_finished = Signal(int, bool, str)

    def __init__(self, device_index: int, port_name: str, baud_rate: int):
        super().__init__()
        self.device_index = device_index
        self._port_name = port_name
        self._baud_rate = baud_rate
        self._serial: Optional[QSerialPort] = None
        self._decoder = FrameDecoder(max_buffer_size=MAX_BUFFER_SIZE)
        # 兼容旧调试代码；缓冲区的所有权属于 FrameDecoder。
        self._buffer = self._decoder.buffer
        self._n_pixel = 0
        self._n_start_pixel = 0
        self._n_valid_pixel = 0
        self.latest_pixels = None
        self.latest_frame_metadata = None
        self._last_frame_emit_ns = 0
        self._intentionally_skipped = 0

    @Slot()
    def do_connect(self):
        try:
            self._serial = QSerialPort()
            self._serial.setPortName(self._port_name)
            self._serial.setBaudRate(self._baud_rate)
            self._serial.setDataBits(_serial_enum("DataBits", "Data8"))
            self._serial.setParity(_serial_enum("Parity", "NoParity"))
            self._serial.setStopBits(_serial_enum("StopBits", "OneStop"))
            self._serial.setFlowControl(_serial_enum("FlowControl", "NoFlowControl"))

            if hasattr(QSerialPort, "ReadWrite"):
                read_write = QSerialPort.ReadWrite
            elif hasattr(QtCore.QIODevice, "ReadWrite"):
                read_write = QtCore.QIODevice.ReadWrite
            else:
                read_write = QtCore.QIODevice.OpenModeFlag.ReadWrite
            if self._serial.open(read_write):
                self._serial.readyRead.connect(self._on_ready_read)
                self._serial.errorOccurred.connect(self._on_error)
                self.open_finished.emit(self.device_index, True, "")
            else:
                self.open_finished.emit(
                    self.device_index, False, self._serial.errorString()
                )
        except Exception as exc:
            self.open_finished.emit(self.device_index, False, str(exc))

    @Slot()
    def do_close(self):
        if self._serial and self._serial.isOpen():
            self._serial.close()
        self._decoder.reset()
        self._last_frame_emit_ns = 0
        self._intentionally_skipped = 0

    @Slot(QtCore.QByteArray)
    def do_write(self, data):
        if self._serial and self._serial.isOpen():
            self._serial.write(data)

    @Slot(int, QtCore.QByteArray)
    def do_send_command(self, cmd: int, params):
        if cmd in (int(CmdCode.START_SINGLE), int(CmdCode.START_CONTINUOUS)):
            self._last_frame_emit_ns = 0
            self._intentionally_skipped = 0
        self.do_write(QtCore.QByteArray(build_packet(cmd, bytes(params))))

    @Slot(int, int, int)
    def set_pixel_info(self, n_pixel: int, n_start_pixel: int, n_valid_pixel: int):
        self._n_pixel = n_pixel
        self._n_start_pixel = n_start_pixel
        self._n_valid_pixel = n_valid_pixel

    def _on_ready_read(self):
        data = bytes(self._serial.readAll())
        previous_overflows = self._decoder.overflow_count
        previous_invalid = self._decoder.invalid_headers
        frames = self._decoder.feed(data)

        if self._decoder.overflow_count > previous_overflows:
            self.packet_error.emit(self.device_index, "串口缓冲区溢出，已重新同步")
        if self._decoder.invalid_headers > previous_invalid:
            self.packet_error.emit(
                self.device_index, "检测到非法帧头或长度，已重新同步"
            )

        for packet_data in frames:
            self._handle_packet(packet_data)

    def _parse_buffer(self):
        """解析缓冲区中已到齐的帧（兼容旧私有接口）。"""

        for packet_data in self._decoder.feed(b""):
            self._handle_packet(packet_data)

    def _handle_packet(self, packet_data: bytes):
        try:
            if packet_data[3] == CmdCode.DATA_TRANSMIT:
                now_ns = time.monotonic_ns()
                if (
                    self._last_frame_emit_ns
                    and now_ns - self._last_frame_emit_ns
                    < FRAME_EMIT_INTERVAL_NS
                ):
                    self._intentionally_skipped += 1
                    return
                parsed = parse_data_packet(
                    packet_data,
                    n_pixel=self._n_pixel,
                    n_start_pixel=self._n_start_pixel,
                    n_valid_pixel=self._n_valid_pixel,
                )
                pixels = parsed["pixels"]
                self.latest_pixels = pixels
                self.latest_frame_metadata = {
                    "packet_number": parsed["packet_number"],
                    "frame_sequence": parsed["frame_sequence"],
                    "reserved": parsed["reserved"],
                    "source_pixel_count": parsed["source_pixel_count"],
                    "packet_number_bits": parsed["packet_number_bits"],
                    "protocol_variant": parsed["protocol_variant"],
                }
                self.frame_received.emit(
                    SpectrumFrame.create(
                        self.device_index,
                        (parsed["frame_sequence"] << 8) | parsed["reserved"],
                        pixels,
                        sequence_bits=parsed["packet_number_bits"] if parsed["packet_number_bits"] == 16 else 24,
                        intentionally_skipped=self._intentionally_skipped,
                    )
                )
                self._intentionally_skipped = 0
                self._last_frame_emit_ns = now_ns
                # 当前阶段统一门控到 20 FPS，避免 Qt 主线程事件队列积压。
                self.data_received.emit(self.device_index, pixels)
            else:
                cmd, params = parse_packet(packet_data)
                self.response_ready.emit(self.device_index, cmd, params)
        except (IndexError, ValueError) as exc:
            self.packet_error.emit(self.device_index, str(exc))

    def _on_error(self, error):
        if error == _serial_enum("SerialPortError", "ResourceError"):
            self.connection_lost.emit(self.device_index)


class DeviceFinder(QtCore.QObject):
    """枚举串口并按常见 USB 串口特征识别候选设备。"""

    devices_found = Signal(list)

    @staticmethod
    def list_available_ports() -> list:
        ports = []
        for info in QSerialPortInfo.availablePorts():
            ports.append(
                {
                    "port_name": info.portName(),
                    "description": info.description(),
                    "manufacturer": info.manufacturer(),
                    "serial_number": info.serialNumber(),
                    "vendor_id": info.vendorIdentifier(),
                    "product_id": info.productIdentifier(),
                    "system_location": info.systemLocation(),
                }
            )
        return ports

    @staticmethod
    def is_likely_spectrometer(port_info: dict) -> bool:
        return (
            port_info.get("vendor_id") == CH569W_VENDOR_ID
            and port_info.get("product_id") == CH569W_PRODUCT_ID
        )
