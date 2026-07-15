"""
采集引擎 — 控制采集生命周期、数据缓冲与分发。
"""

import time
import threading
from collections import deque
from typing import Optional, Dict, Callable
import numpy as np

from ..qt import QtCore, Signal

QObject = QtCore.QObject
QTimer = QtCore.QTimer


class AcquisitionEngine(QObject):
    """采集控制引擎 — 支持每设备独立启停"""

    device_acq_started = Signal(int)       # (device_id) 单设备采集开始
    device_acq_stopped = Signal(int)       # (device_id) 单设备采集停止
    frame_collected = Signal(int, int)     # (device_id, frame_count)
    storage_triggered = Signal(int, object, object)  # (device_id, wl, pixels)

    def __init__(self, device_manager):
        super().__init__()
        self.dm = device_manager
        self._acquiring_devices: set = set()   # 正在采集的设备ID集合
        self._single_shot_devices: set = set() # 单次采集设备

        # 帧计数 (按设备)
        self.frame_counts: Dict[int, int] = {}

        # 自动存储设置
        self.auto_save_enabled = False
        self.auto_save_mode = 'immediate'
        self.auto_save_count = 10
        self.auto_save_interval_s = 5.0
        self._last_save_time = 0.0
        self._frames_since_save: Dict[int, int] = {}

        # 缓冲队列
        self.buffer: deque = deque(maxlen=5000)

        self._save_timer = QTimer(self)
        self._save_timer.timeout.connect(self._on_save_timer)

        self.dm.data_arrived.connect(self._on_data_arrived)

    def _clear_buffer_for_device(self, device_id: int):
        """清理指定设备的历史缓冲数据"""
        if not self.buffer:
            return
        self.buffer = deque(
            (item for item in self.buffer if item[0] != device_id),
            maxlen=self.buffer.maxlen)

    # ========== 单设备控制 ==========

    def start_device(self, device_id: int, continuous: bool = True):
        """启动单个设备采集"""
        if device_id in self._acquiring_devices:
            return

        self._clear_buffer_for_device(device_id)
        self._acquiring_devices.add(device_id)
        if not continuous:
            self._single_shot_devices.add(device_id)

        self.frame_counts[device_id] = 0
        self._frames_since_save[device_id] = 0

        device = self.dm.get_device(device_id)
        if device:
            device.latest_pixels = None
            device.latest_wavelengths = None
            device.acquiring = True

        self.dm.start_acquisition(device_id, continuous)
        self.device_acq_started.emit(device_id)

        if self.auto_save_enabled and self.auto_save_mode == 'time' and len(self._acquiring_devices) == 1:
            self._save_timer.start(int(self.auto_save_interval_s * 1000))

    def stop_device(self, device_id: int):
        """停止单个设备采集"""
        if device_id not in self._acquiring_devices:
            return

        self._acquiring_devices.discard(device_id)
        self._single_shot_devices.discard(device_id)

        device = self.dm.get_device(device_id)
        if device:
            device.acquiring = False

        self.dm.stop_acquisition(device_id)
        self.device_acq_stopped.emit(device_id)

        if not self._acquiring_devices:
            self._save_timer.stop()

    def is_device_running(self, device_id: int) -> bool:
        return device_id in self._acquiring_devices

    # ========== 全局控制 (向后兼容) ==========

    def start(self, continuous: bool = True, device_ids: Optional[list] = None):
        """启动所有(或指定)设备采集"""
        if device_ids is None:
            device_ids = [d.device_id for d in self.dm.get_connected_devices()]
        for did in device_ids:
            self.start_device(did, continuous)

    def stop(self):
        """停止所有设备采集"""
        for did in list(self._acquiring_devices):
            self.stop_device(did)

    def is_running(self) -> bool:
        return len(self._acquiring_devices) > 0

    # ========== 内部 ==========

    def _on_data_arrived(self, device_id: int, wavelengths: np.ndarray, pixels: np.ndarray):
        """收到新帧数据"""
        if device_id not in self._acquiring_devices:
            return

        self.frame_counts[device_id] = self.frame_counts.get(device_id, 0) + 1
        frame_num = self.frame_counts[device_id]

        self.frame_collected.emit(device_id, frame_num)
        self.buffer.append((device_id, wavelengths.copy(), pixels.copy(), time.time()))

        # 自动存储
        if self.auto_save_enabled:
            self._frames_since_save[device_id] = self._frames_since_save.get(device_id, 0) + 1
            if self.auto_save_mode == 'immediate':
                self.storage_triggered.emit(device_id, wavelengths, pixels)
            elif self.auto_save_mode == 'count':
                if self._frames_since_save[device_id] >= self.auto_save_count:
                    self._frames_since_save[device_id] = 0
                    self.storage_triggered.emit(device_id, wavelengths, pixels)

        # 单次采集: 收到数据后自动停止该设备
        if device_id in self._single_shot_devices:
            QTimer.singleShot(500, lambda: self.stop_device(device_id))

    def _on_save_timer(self):
        """定时存储触发"""
        if not self._acquiring_devices:
            return
        now = time.time()
        if now - self._last_save_time >= self.auto_save_interval_s:
            self._last_save_time = now
            for device_id in list(self._acquiring_devices):
                device = self.dm.get_device(device_id)
                if device and device.latest_wavelengths is not None and device.latest_pixels is not None:
                    self.storage_triggered.emit(
                        device_id, device.latest_wavelengths, device.latest_pixels)


class DataProcessor(QObject):
    """光谱数据处理 — 背景扣除、吸光度计算、自定义公式"""

    def __init__(self, device_manager):
        super().__init__()
        self.dm = device_manager

    def get_processed_data(self, device_id: int, mode: str = 'raw',
                           custom_formula: str = '') -> Optional[tuple]:
        """获取处理后的数据"""
        device = self.dm.get_device(device_id)
        if device is None:
            return None
        return device.get_display_spectrum(mode, custom_formula)

    def store_background(self, device_id: int):
        """存储当前光谱为背景光谱 (Idark)"""
        device = self.dm.get_device(device_id)
        if device and device.latest_pixels is not None:
            device.background_spectrum = device.latest_pixels.copy()

    def store_reference(self, device_id: int):
        """存储当前光谱为参考光谱 (I0)"""
        device = self.dm.get_device(device_id)
        if device and device.latest_pixels is not None:
            device.reference_spectrum = device.latest_pixels.copy()

    def set_intensity_calibration(self, device_id: int, coeffs: np.ndarray):
        """设置强度校准系数数组"""
        device = self.dm.get_device(device_id)
        if device:
            device.intensity_calib = coeffs.copy()
