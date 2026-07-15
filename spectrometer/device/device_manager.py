"""
多设备管理器 — 管理多台光谱仪的连接、同步与参数设置。
每个设备的串口通信运行在独立 QThread 中。
"""

from typing import List, Optional, Dict
import numpy as np

from PyQt5.QtCore import QObject, pyqtSignal, QThread, QMetaObject, Qt, Q_ARG, QTimer

from .spectrometer import SpectrometerDevice, DeviceInfo, CalibrationData
from ..communication.serial_port import SerialWorker
from ..communication.protocol import CmdCode, build_packet


class DeviceManager(QObject):
    """多台光谱仪管理器"""

    device_added = pyqtSignal(int)
    device_removed = pyqtSignal(int)
    device_updated = pyqtSignal(int)
    device_connected = pyqtSignal(int)             # 设备线程连接成功
    device_connect_failed = pyqtSignal(int, str)   # (device_id, error_msg)
    calib_ready = pyqtSignal(int, float, float, float, float)  # (device_id, c1,c2,c3,c4)
    data_arrived = pyqtSignal(int, np.ndarray, np.ndarray)
    error_occurred = pyqtSignal(int, str)

    def __init__(self):
        super().__init__()
        self.devices: Dict[int, SpectrometerDevice] = {}
        self._workers: Dict[int, SerialWorker] = {}
        self._threads: Dict[int, QThread] = {}
        self._next_id = 0

        # 数据处理节流: 30fps 轮询 worker.latest_pixels 统一做 numpy 转换
        self._process_timer = QTimer(self)
        self._process_timer.timeout.connect(self._on_process_tick)
        self._process_timer.start(33)

        # 同步控制
        self.global_sync_enabled: bool = False
        self.sync_mode: str = 'soft'
        self.master_device_id: Optional[int] = None

    def add_and_connect(self, port_name: str, baud_rate: int = 115200) -> int:
        """添加设备并启动连接线程, 返回 device_id"""
        # 检查已存在同端口设备
        for did, dev in self.devices.items():
            if dev.port_name == port_name:
                if not dev.connected:
                    self.remove_device(did)
                    break
                else:
                    self.device_updated.emit(did)
                    return did

        device_id = self._next_id
        self._next_id += 1

        device = SpectrometerDevice(device_id, port_name)
        self.devices[device_id] = device

        # 创建 Worker (无 parent, 供 moveToThread)
        worker = SerialWorker(device_id, port_name, baud_rate)
        self._workers[device_id] = worker

        # 创建独立线程
        thread = QThread()
        self._threads[device_id] = thread
        worker.moveToThread(thread)

        # 跨线程信号连接 (数据不走信号, 由 _on_process_tick 轮询 worker.latest_pixels)
        worker.packet_error.connect(self._on_error)
        worker.response_ready.connect(self._on_response)
        worker.connection_lost.connect(self._on_disconnect)
        worker.open_finished.connect(self._on_open_finished)

        # 线程生命周期
        thread.started.connect(worker.do_connect)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        thread.start()
        self.device_added.emit(device_id)
        return device_id

    def remove_device(self, device_id: int):
        """断开并移除设备"""
        worker = self._workers.get(device_id)
        thread = self._threads.get(device_id)

        if worker and thread and thread.isRunning():
            # 在线程中关闭串口, 然后退出线程
            QMetaObject.invokeMethod(worker, 'do_close', Qt.QueuedConnection)
            thread.quit()
            thread.wait(2000)  # 最多等待2秒

        self._workers.pop(device_id, None)
        self._threads.pop(device_id, None)
        self.devices.pop(device_id, None)
        self.device_removed.emit(device_id)

    def disconnect_device(self, device_id: int):
        """断开设备连接 (同 remove, 线程不可重用)"""
        self.remove_device(device_id)

    def remove_all_devices(self):
        for did in list(self.devices.keys()):
            self.remove_device(did)

    def get_device(self, device_id: int) -> Optional[SpectrometerDevice]:
        return self.devices.get(device_id)

    def get_all_devices(self) -> List[SpectrometerDevice]:
        return sorted(self.devices.values(), key=lambda d: d.start_wavelength)

    def get_connected_devices(self) -> List[SpectrometerDevice]:
        return [d for d in self.devices.values() if d.connected]

    def send_to_device(self, device_id: int, cmd: int, params: bytes = b'') -> bool:
        """向指定设备发送命令 (跨线程安全)"""
        worker = self._workers.get(device_id)
        if worker is None:
            return False
        packet = build_packet(cmd, params)
        QMetaObject.invokeMethod(
            worker, 'do_write', Qt.QueuedConnection, Q_ARG(bytes, packet))
        return True

    def broadcast_command(self, cmd: int, params: bytes = b'',
                          device_ids: Optional[List[int]] = None):
        for did in (device_ids or list(self.devices.keys())):
            self.send_to_device(did, cmd, params)

    # ---- 便捷命令方法 ----

    def init_device(self, device_id: int):
        return self.send_to_device(device_id, 0x10)

    def set_integration_time(self, device_id: int, time_us: int):
        device = self.devices.get(device_id)
        if device:
            device.integration_time_us = time_us
        return self.send_to_device(device_id, 0x20, self._pack('<I', time_us))

    def set_trigger_mode(self, device_id: int, mode: int):
        device = self.devices.get(device_id)
        if device:
            device.trigger_mode = mode
        return self.send_to_device(device_id, 0x21, self._pack('<B', mode))

    def set_gain(self, device_id: int, gain: int):
        device = self.devices.get(device_id)
        if device:
            device.gain = gain
        return self.send_to_device(device_id, 0x25, self._pack('<B', gain))

    def set_avg_count(self, device_id: int, count: int):
        device = self.devices.get(device_id)
        if device:
            device.avg_count = count
        return self.send_to_device(device_id, 0x23, self._pack('<I', count))

    def set_delay(self, device_id: int, delay_us: int):
        device = self.devices.get(device_id)
        if device:
            device.delay_us = delay_us
        return self.send_to_device(device_id, 0x2b, self._pack('<I', delay_us))

    def set_calib_coeff(self, device_id: int,
                        c1: float, c2: float, c3: float, c4: float):
        device = self.devices.get(device_id)
        if device:
            device.wavelength_calib = CalibrationData(c1, c2, c3, c4)
            device.update_start_wavelength()
        # 协议顺序: C4(3阶),C3(2阶),C2(1阶),C1(0阶) — 发送时反转
        return self.send_to_device(device_id, 0x27, self._pack('<4f', c4, c3, c2, c1))

    def start_acquisition(self, device_id: int, continuous: bool = True):
        cmd = 0x51 if continuous else 0x50
        device = self.devices.get(device_id)
        if device:
            device.acquiring = True
        return self.send_to_device(device_id, cmd)

    def stop_acquisition(self, device_id: int):
        device = self.devices.get(device_id)
        if device:
            device.acquiring = False
        return self.send_to_device(device_id, 0x52)

    def start_sync_acquisition(self, continuous: bool = True):
        cmd = 0x51 if continuous else 0x50
        devices = self.get_connected_devices()
        if not devices:
            return
        if self.global_sync_enabled and self.sync_mode == 'soft':
            return self.broadcast_command(cmd)
        elif self.global_sync_enabled and self.master_device_id is not None:
            return self.send_to_device(self.master_device_id, cmd)
        else:
            return self.broadcast_command(cmd)

    def stop_all(self):
        self.broadcast_command(0x52)

    def query_calibration(self, device_id: int):
        return self.send_to_device(device_id, 0x37)

    def query_version(self, device_id: int):
        return self.send_to_device(device_id, 0x01)

    def query_trigger_mode(self, device_id: int):
        return self.send_to_device(device_id, 0x31)

    def query_integration_time(self, device_id: int):
        return self.send_to_device(device_id, 0x30)

    def query_serial_number(self, device_id: int):
        """查询产品序列号 (0x3C) — 前32字节ASCII序列号 + 200字节其他信息"""
        return self.send_to_device(device_id, 0x3c)

    # ---- 内部槽函数 ----

    def _on_open_finished(self, device_id: int, success: bool, error_msg: str):
        """连接线程完成回调 (主线程)"""
        device = self.devices.get(device_id)
        if not device:
            return
        if success:
            device.connected = True
            self.device_connected.emit(device_id)
        else:
            device.connected = False
            err = error_msg if error_msg else f"无法打开串口 {device.port_name}"
            self.device_connect_failed.emit(device_id, err)

    def _on_process_tick(self):
        """30fps 定时轮询 — 从 worker 直接读最新像素, 无跨线程信号, 做 numpy 转换并分发"""
        for device_id, worker in list(self._workers.items()):
            pixels = worker.latest_pixels
            if not pixels:
                continue
            worker.latest_pixels = None  # 消费掉
            device = self.devices.get(device_id)
            if not device:
                continue
            device.set_latest_data(pixels)
            if device.latest_pixels is not None:
                pixel_indices = np.arange(len(device.latest_pixels), dtype=np.float64)
                self.data_arrived.emit(
                    device_id, pixel_indices, device.latest_pixels.copy())

    def _on_response(self, device_id: int, cmd: int, params: bytes):
        """收到命令响应"""
        device = self.devices.get(device_id)
        if not device:
            return

        import struct as st

        is_set_cmd = (cmd == CmdCode.DEVICE_INIT or
                      (0x20 <= cmd <= 0x2c) or
                      (0x50 <= cmd <= 0x54 and cmd != 0x53))

        if is_set_cmd:
            if len(params) < 1:
                return
            from ..communication.protocol import check_status
            status = check_status(params[0])
            if status != 0:
                self.error_occurred.emit(device_id, f"命令 0x{cmd:02X} 返回失败")
                return
            params = params[1:]

        if cmd == 0x01 and len(params) >= 32:
            vals = list(st.unpack('<IIffIIII', params[:32]))
            if len(params) >= 40:
                extra = st.unpack('<II', params[32:40])
                vals.extend(extra)
            while len(vals) < 10:
                vals.append(0)
            device.info = DeviceInfo(
                name=vals[0], dev_type=vals[1], hw_ver=vals[2], fw_ver=vals[3],
                serial_num=vals[4], pixel_count=vals[5], start_pixel=vals[6],
                valid_pixel=vals[7], pos_time_min=vals[8], pos_time_max=vals[9])
            device.initialized = True
            device.update_start_wavelength()

            # 同步像素信息到 Worker 线程, 用于数据包解析裁剪
            worker = self._workers.get(device_id)
            if worker:
                QMetaObject.invokeMethod(
                    worker, 'set_pixel_info', Qt.QueuedConnection,
                    Q_ARG(int, device.info.pixel_count),
                    Q_ARG(int, device.info.start_pixel),
                    Q_ARG(int, device.info.valid_pixel))

        elif cmd == 0x37 and len(params) >= 16:
            # 设备返回顺序: C4,C3,C2,C1 → 反转为 C1(0阶),C2(1阶),C3(2阶),C4(3阶)
            c = st.unpack('<4f', params[:16])
            device.wavelength_calib = CalibrationData(c[3], c[2], c[1], c[0])
            device.update_start_wavelength()
            self.calib_ready.emit(device_id, c[3], c[2], c[1], c[0])

        elif cmd == 0x30 and len(params) >= 4:
            device.integration_time_us = st.unpack('<I', params[:4])[0]

        elif cmd == 0x31 and len(params) >= 1:
            device.trigger_mode = params[0]

        elif cmd == 0x32 and len(params) >= 4:
            device.interval_us = st.unpack('<I', params[:4])[0]

        elif cmd == 0x33 and len(params) >= 4:
            device.avg_count = st.unpack('<I', params[:4])[0]

        elif cmd == 0x35 and len(params) >= 1:
            device.gain = params[0]

        elif cmd == 0x3b and len(params) >= 4:
            device.delay_us = st.unpack('<I', params[:4])[0]

        elif cmd == 0x3c and len(params) >= 32:
            # 前32字节 = ASCII生产序列号, 后200字节 = 其他信息
            raw = bytes(params)
            sn_bytes = raw[:32].rstrip(b'\x00')
            try:
                sn = sn_bytes.decode('ascii', errors='replace').replace('\x00', '').strip()
            except Exception:
                sn = ''
            # 全'0'表示未写入
            if sn and not all(c == '0' for c in sn):
                device.info.prod_serial = sn
            # 从其他信息探测传感器描述
            extra = raw[32:232] if len(raw) > 32 else b''
            try:
                extra_str = extra.decode('ascii', errors='ignore')
                for kw in ['CCD', 'CMOS', 'InGaAs', 'Si', 'NMOS', 'PDA']:
                    if kw.lower() in extra_str.lower():
                        device.info.sensor_type = kw.upper()
                        break
            except Exception:
                pass

        self.device_updated.emit(device_id)

    def _on_error(self, device_id: int, error_msg: str):
        self.error_occurred.emit(device_id, error_msg)

    def _on_disconnect(self, device_id: int):
        device = self.devices.get(device_id)
        if device:
            device.connected = False
            device.acquiring = False
        self.device_updated.emit(device_id)

    @staticmethod
    def _pack(fmt: str, *args) -> bytes:
        import struct
        return struct.pack(fmt, *args)
