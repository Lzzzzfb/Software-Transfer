"""串口通信：每台设备在独立 QThread 中完成非阻塞 I/O。"""

from typing import Optional

from PyQt5.QtCore import QObject, QThread, pyqtSignal, pyqtSlot
from PyQt5.QtSerialPort import QSerialPort, QSerialPortInfo

from .frame_decoder import FrameDecoder
from .protocol import CmdCode, build_packet, parse_data_packet, parse_packet


MAX_BUFFER_SIZE = 2 * 1024 * 1024


class SerialWorker(QObject):
    """单台光谱仪的串口工作对象。"""

    data_received = pyqtSignal(int, list)
    packet_error = pyqtSignal(int, str)
    connection_lost = pyqtSignal(int)
    response_ready = pyqtSignal(int, int, bytes)
    open_finished = pyqtSignal(int, bool, str)

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

    @pyqtSlot()
    def do_connect(self):
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
                self.open_finished.emit(self.device_index, True, "")
            else:
                self.open_finished.emit(
                    self.device_index, False, self._serial.errorString()
                )
        except Exception as exc:
            self.open_finished.emit(self.device_index, False, str(exc))

    @pyqtSlot()
    def do_close(self):
        if self._serial and self._serial.isOpen():
            self._serial.close()
        self._decoder.reset()

    @pyqtSlot(bytes)
    def do_write(self, data: bytes):
        if self._serial and self._serial.isOpen():
            self._serial.write(data)

    @pyqtSlot(int, bytes)
    def do_send_command(self, cmd: int, params: bytes):
        self.do_write(build_packet(cmd, params))

    @pyqtSlot(int, int, int)
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
                }
                # 逐帧信号供无丢帧存储链路使用；显示仍可读取 latest_pixels 节流。
                self.data_received.emit(self.device_index, pixels)
            else:
                cmd, params = parse_packet(packet_data)
                self.response_ready.emit(self.device_index, cmd, params)
        except (IndexError, ValueError) as exc:
            self.packet_error.emit(self.device_index, str(exc))

    def _on_error(self, error: QSerialPort.SerialPortError):
        if error == QSerialPort.ResourceError:
            self.connection_lost.emit(self.device_index)


class DeviceFinder(QObject):
    """枚举串口并按常见 USB 串口特征识别候选设备。"""

    devices_found = pyqtSignal(list)

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
        known_vids = {0x0483, 0x1A86, 0x10C4, 0x067B, 0x0403}
        vid = port_info.get("vendor_id", 0)
        description = port_info.get("description", "").lower()
        manufacturer = port_info.get("manufacturer", "").lower()
        if vid in known_vids:
            return True
        keywords = (
            "usb serial",
            "vcp",
            "stmicro",
            "ch340",
            "cp210",
            "ftdi",
            "spectrometer",
        )
        return any(word in description or word in manufacturer for word in keywords)
