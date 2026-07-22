"""多设备连接、协议识别、状态、参数 ACK 和显示节流管理。"""

import math
import struct
import time
from typing import Dict, List, Optional

import numpy as np

from ..communication.protocol import CmdCode, TriggerMode, build_packet, check_status
from ..communication.serial_port import SerialWorker
from ..domain.models import SpectrumFrame
from ..qt import QtCore, Signal
from .spectrometer import CalibrationData, DeviceInfo, SpectrometerDevice


class DeviceManager(QtCore.QObject):
    device_added = Signal(int)
    device_removed = Signal(int)
    device_updated = Signal(int)
    device_connected = Signal(int)
    device_connect_failed = Signal(int, str)
    calib_ready = Signal(int, float, float, float, float)
    data_arrived = Signal(int, object, object)
    frame_arrived = Signal(object)
    error_occurred = Signal(int, str)
    diagnostic_event = Signal(str)
    sync_started = Signal(object)
    sync_configuration_failed = Signal(str)
    command_completed = Signal(int, int, bool, object)

    def __init__(self):
        super().__init__()
        self.devices: Dict[int, SpectrometerDevice] = {}
        self._workers: Dict[int, SerialWorker] = {}
        self._threads: Dict[int, QtCore.QThread] = {}
        self._next_id = 0
        self._latest_frames: Dict[int, SpectrumFrame] = {}
        self._pending_updates: Dict[tuple, callable] = {}
        self._acquisition_requested = set()
        self._reconnect_attempts: Dict[int, int] = {}
        self._removing = set()
        self._probing = set()
        self._announced_devices = set()
        self._probe_tokens: Dict[int, int] = {}
        self._probe_failures: Dict[str, int] = {}
        self._probe_retry_after: Dict[str, float] = {}
        self._sync_generation = 0
        self._sync_setup = None

        self._process_timer = QtCore.QTimer(self)
        self._process_timer.timeout.connect(self._on_process_tick)
        self._process_timer.start(33)

        self.global_sync_enabled = False
        self.sync_mode = "soft"
        self.master_device_id: Optional[int] = None

    def add_and_connect(
        self, port_name: str, baud_rate: int = 115200, force: bool = False
    ) -> int:
        port_key = port_name.upper()
        if force:
            self._probe_failures.pop(port_key, None)
            self._probe_retry_after.pop(port_key, None)
        elif time.monotonic() < self._probe_retry_after.get(port_key, 0):
            return -1

        for device_id, device in self.devices.items():
            if device.port_name == port_name:
                if device.connected or device_id in self._probing:
                    self.device_updated.emit(device_id)
                    return device_id
                self._schedule_reconnect(device_id, immediate=True)
                return device_id

        device_id = self._next_id
        self._next_id += 1
        self.devices[device_id] = SpectrometerDevice(device_id, port_name)
        self._create_worker(device_id, port_name, baud_rate)
        return device_id

    def add_simulated_device(
        self,
        port_name: str,
        serial_number: str,
        pixel_count: int,
        wavelength_start: float,
        wavelength_step: float,
    ) -> int:
        device_id = self._next_id
        self._next_id += 1
        device = SpectrometerDevice(device_id, port_name)
        device.connected = True
        device.initialized = True
        device.info = DeviceInfo(
            name=0x5A474341,
            dev_type=2,
            hw_ver=1.0,
            fw_ver=1.0,
            serial_num=device_id + 1,
            pixel_count=pixel_count,
            start_pixel=0,
            valid_pixel=pixel_count,
            pos_time_min=10,
            pos_time_max=10_000_000,
            prod_serial=serial_number,
            sensor_type="CMOS",
        )
        device.wavelength_calib = CalibrationData(wavelength_start, wavelength_step, 0, 0)
        device.update_start_wavelength()
        self.devices[device_id] = device
        self._announced_devices.add(device_id)
        self.device_added.emit(device_id)
        self.device_connected.emit(device_id)
        self.device_updated.emit(device_id)
        return device_id

    def inject_simulated_frame(self, frame: SpectrumFrame) -> None:
        self._on_frame_received(frame)

    def _create_worker(self, device_id: int, port_name: str, baud_rate: int) -> None:
        worker = SerialWorker(device_id, port_name, baud_rate)
        thread = QtCore.QThread()
        self._workers[device_id] = worker
        self._threads[device_id] = thread
        worker.moveToThread(thread)
        worker.frame_received.connect(self._on_frame_received)
        worker.packet_error.connect(self._on_error)
        worker.response_ready.connect(self._on_response)
        worker.connection_lost.connect(self._on_disconnect)
        worker.open_finished.connect(self._on_open_finished)
        thread.started.connect(worker.do_connect)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def remove_device(self, device_id: int, preserve_probe_backoff: bool = False):
        device = self.devices.get(device_id)
        port_key = device.port_name.upper() if device else ""
        was_announced = device_id in self._announced_devices
        self._removing.add(device_id)
        worker = self._workers.get(device_id)
        thread = self._threads.get(device_id)
        if worker and thread and thread.isRunning():
            QtCore.QMetaObject.invokeMethod(
                worker, "do_close", QtCore.Qt.QueuedConnection
            )
            thread.quit()
            if not thread.wait(2000):
                self.error_occurred.emit(device_id, "串口线程未在 2 秒内结束")
        self._workers.pop(device_id, None)
        self._threads.pop(device_id, None)
        self._latest_frames.pop(device_id, None)
        self._acquisition_requested.discard(device_id)
        self.devices.pop(device_id, None)
        self._reconnect_attempts.pop(device_id, None)
        self._probing.discard(device_id)
        self._probe_tokens.pop(device_id, None)
        self._announced_devices.discard(device_id)
        if port_key and not preserve_probe_backoff:
            self._probe_failures.pop(port_key, None)
            self._probe_retry_after.pop(port_key, None)
        for key in [key for key in self._pending_updates if key[0] == device_id]:
            self._pending_updates.pop(key, None)
        self._removing.discard(device_id)
        if was_announced:
            self.device_removed.emit(device_id)

    disconnect_device = remove_device

    def remove_all_devices(self):
        for device_id in list(self.devices):
            self.remove_device(device_id)

    def get_device(self, device_id: int) -> Optional[SpectrometerDevice]:
        return self.devices.get(device_id)

    def get_all_devices(self) -> List[SpectrometerDevice]:
        return sorted(self.devices.values(), key=lambda item: item.start_wavelength)

    def get_connected_devices(self) -> List[SpectrometerDevice]:
        return [
            device
            for device in self.devices.values()
            if device.connected and device.initialized
        ]

    @property
    def acquisition_active(self) -> bool:
        return bool(
            self._sync_setup
            or self._acquisition_requested
            or any(device.acquiring for device in self.devices.values())
        )

    def send_to_device(self, device_id: int, cmd: int, params: bytes = b"") -> bool:
        device = self.devices.get(device_id)
        if (device and (device.acquiring or device_id in self._acquisition_requested)
                and int(cmd) != int(CmdCode.STOP_ACQUISITION)):
            self.error_occurred.emit(device_id, "采集中不能发送参数或查询命令，请先停止采集")
            return False
        worker = self._workers.get(device_id)
        if worker is None:
            # 模拟设备视为成功接收，便于全流程演示。
            return device_id in self.devices and self.devices[device_id].port_name.startswith("SIM")
        packet = build_packet(cmd, params)
        QtCore.QMetaObject.invokeMethod(
            worker,
            "do_write",
            QtCore.Qt.QueuedConnection,
            QtCore.Q_ARG(QtCore.QByteArray, QtCore.QByteArray(packet)),
        )
        return True

    def broadcast_command(
        self, cmd: int, params: bytes = b"", device_ids: Optional[List[int]] = None
    ):
        return [
            self.send_to_device(device_id, cmd, params)
            for device_id in (device_ids or list(self.devices))
        ]

    def _set_after_ack(self, device_id: int, cmd: int, params: bytes, apply) -> bool:
        if not self.send_to_device(device_id, cmd, params):
            return False
        if self.devices[device_id].port_name.startswith("SIM"):
            apply()
            self.device_updated.emit(device_id)
            if int(cmd) == int(CmdCode.SET_TRIG_MODE):
                self._complete_sync_trigger_mode(device_id, True)
            self.command_completed.emit(device_id, int(cmd), True, b"\x60")
        else:
            self._pending_updates[(device_id, int(cmd))] = apply
        return True

    def init_device(self, device_id: int):
        return self.send_to_device(device_id, CmdCode.DEVICE_INIT)

    def set_integration_time(self, device_id: int, time_us: int):
        return self._set_after_ack(
            device_id,
            CmdCode.SET_INTEG_TIME,
            struct.pack("<I", time_us),
            lambda: setattr(self.devices[device_id], "integration_time_us", time_us),
        )

    def set_trigger_mode(self, device_id: int, mode: int):
        if mode not in tuple(item.value for item in TriggerMode):
            raise ValueError("触发模式只能是 0、1、2")
        return self._set_after_ack(
            device_id,
            CmdCode.SET_TRIG_MODE,
            struct.pack("<B", mode),
            lambda: setattr(self.devices[device_id], "trigger_mode", mode),
        )

    def set_interval(self, device_id: int, interval_us: int):
        return self._set_after_ack(
            device_id,
            CmdCode.SET_INTERVAL,
            struct.pack("<I", interval_us),
            lambda: setattr(self.devices[device_id], "interval_us", interval_us),
        )

    def set_gain(self, device_id: int, gain: int):
        return self._set_after_ack(
            device_id,
            CmdCode.SET_GAIN,
            struct.pack("<B", gain),
            lambda: setattr(self.devices[device_id], "gain", gain),
        )

    def set_avg_count(self, device_id: int, count: int):
        return self._set_after_ack(
            device_id,
            CmdCode.SET_AVG_COUNT,
            struct.pack("<I", count),
            lambda: setattr(self.devices[device_id], "avg_count", count),
        )

    def set_delay(self, device_id: int, delay_us: int):
        return self._set_after_ack(
            device_id,
            CmdCode.SET_DELAY,
            struct.pack("<I", delay_us),
            lambda: setattr(self.devices[device_id], "delay_us", delay_us),
        )

    def set_calib_coeff(self, device_id: int, c1: float, c2: float, c3: float, c4: float):
        def apply():
            device = self.devices[device_id]
            device.wavelength_calib = CalibrationData(c1, c2, c3, c4)
            device.update_start_wavelength()

        # 下位机协议顺序必须保持 C4、C3、C2、C1。
        return self._set_after_ack(
            device_id,
            CmdCode.SET_CALIB_COEFF,
            struct.pack("<4f", c4, c3, c2, c1),
            apply,
        )

    def start_acquisition(self, device_id: int, continuous: bool = True):
        cmd = CmdCode.START_CONTINUOUS if continuous else CmdCode.START_SINGLE
        result = self.send_to_device(device_id, cmd)
        if result:
            self._acquisition_requested.add(device_id)
        if result and self.devices[device_id].port_name.startswith("SIM"):
            self.devices[device_id].acquiring = True
            self.device_updated.emit(device_id)
            self.command_completed.emit(device_id, int(cmd), True, b"\x60")
        return result

    def stop_acquisition(self, device_id: int):
        result = self.send_to_device(device_id, CmdCode.STOP_ACQUISITION)
        self._acquisition_requested.discard(device_id)
        device = self.devices.get(device_id)
        if device:
            device.acquiring = False
            self.device_updated.emit(device_id)
            if result and device.port_name.startswith("SIM"):
                self.command_completed.emit(
                    device_id, int(CmdCode.STOP_ACQUISITION), True, b"\x60"
                )
        return result

    def start_sync_acquisition(self, continuous: bool = True):
        device_ids = [item.device_id for item in self.get_connected_devices() if item.enabled]
        if self.global_sync_enabled and self.sync_mode == "hard" and self.master_device_id in device_ids:
            device_ids = [item for item in device_ids if item != self.master_device_id] + [self.master_device_id]
        # 下位机自动输出同步脉冲，上位机只按顺序布防/启动，不发送 0x54。
        return [self.start_acquisition(item, continuous) for item in device_ids]

    def prepare_sync_acquisition(self, continuous: bool = True) -> bool:
        """配置硬同步触发模式，收到全部 ACK 后再按顺序启动。"""

        device_ids = [item.device_id for item in self.get_connected_devices() if item.enabled]
        if not device_ids:
            return False
        if not (self.global_sync_enabled and self.sync_mode == "hard"):
            results = self.start_sync_acquisition(continuous)
            if all(results):
                self.sync_started.emit(device_ids)
                return True
            self.sync_configuration_failed.emit("启动命令未能发送到所有设备")
            return False

        internal = self.master_device_id in device_ids
        desired_modes = {
            device_id: (
                TriggerMode.SOFT_MASTER
                if internal and device_id == self.master_device_id
                else TriggerMode.EXTERNAL
            )
            for device_id in device_ids
        }
        self._sync_generation += 1
        generation = self._sync_generation
        self._sync_setup = {
            "generation": generation,
            "pending": set(device_ids),
            "failed": [],
            "continuous": continuous,
            "device_ids": device_ids,
        }
        for index, (device_id, mode) in enumerate(desired_modes.items()):
            QtCore.QTimer.singleShot(
                index * 120,
                lambda did=device_id, value=int(mode), gen=generation:
                    self._send_sync_trigger_mode(gen, did, value),
            )
        QtCore.QTimer.singleShot(
            len(desired_modes) * 120 + 2000,
            lambda: self._sync_setup_timeout(generation),
        )
        return True

    def _send_sync_trigger_mode(self, generation: int, device_id: int, mode: int):
        setup = self._sync_setup
        if not setup or setup["generation"] != generation:
            return
        if not self.set_trigger_mode(device_id, mode):
            self._complete_sync_trigger_mode(device_id, False)

    def _complete_sync_trigger_mode(self, device_id: int, success: bool):
        setup = self._sync_setup
        if not setup or device_id not in setup["pending"]:
            return
        setup["pending"].discard(device_id)
        if not success:
            setup["failed"].append(device_id)
        if setup["pending"]:
            return
        generation = setup["generation"]
        if setup["failed"]:
            failed = ", ".join(str(item) for item in setup["failed"])
            self._sync_setup = None
            self.sync_configuration_failed.emit(f"设备 {failed} 的同步触发模式配置失败")
            return
        QtCore.QTimer.singleShot(50, lambda: self._start_after_sync_setup(generation))

    def _start_after_sync_setup(self, generation: int):
        setup = self._sync_setup
        if not setup or setup["generation"] != generation:
            return
        self._sync_setup = None
        results = self.start_sync_acquisition(setup["continuous"])
        if all(results):
            self.sync_started.emit(setup["device_ids"])
        else:
            self.sync_configuration_failed.emit("同步模式已配置，但启动命令发送不完整")

    def _sync_setup_timeout(self, generation: int):
        setup = self._sync_setup
        if not setup or setup["generation"] != generation:
            return
        pending = ", ".join(str(item) for item in sorted(setup["pending"]))
        self._sync_setup = None
        self.sync_configuration_failed.emit(f"同步配置 ACK 超时，未完成设备：{pending}")

    def stop_all(self):
        self._sync_generation += 1
        self._sync_setup = None
        for device_id in list(self.devices):
            self.stop_acquisition(device_id)

    def query_calibration(self, device_id: int): return self.send_to_device(device_id, CmdCode.QUERY_CALIB_COEFF)
    def query_version(self, device_id: int): return self.send_to_device(device_id, CmdCode.GET_VERSION)
    def query_trigger_mode(self, device_id: int): return self.send_to_device(device_id, CmdCode.QUERY_TRIG_MODE)
    def query_integration_time(self, device_id: int): return self.send_to_device(device_id, CmdCode.QUERY_INTEG_TIME)
    def query_serial_number(self, device_id: int): return self.send_to_device(device_id, CmdCode.QUERY_SERIAL_NUM)

    def _on_open_finished(self, device_id: int, success: bool, error_msg: str):
        device = self.devices.get(device_id)
        if not device:
            return
        if success:
            self._reconnect_attempts[device_id] = 0
            device.connected = False
            device.initialized = False
            device.acquiring = False
            token = self._probe_tokens.get(device_id, 0) + 1
            self._probe_tokens[device_id] = token
            self._probing.add(device_id)
            self.diagnostic_event.emit(
                f"{device.port_name} 串口已打开，正在进行光谱仪协议握手"
            )
            self.query_version(device_id)
            QtCore.QTimer.singleShot(
                2500, lambda: self._on_probe_timeout(device_id, token)
            )
        else:
            device.connected = False
            if device_id in self._announced_devices:
                self.device_connect_failed.emit(
                    device_id, error_msg or f"无法打开 {device.port_name}"
                )
                self._schedule_reconnect(device_id)
                self.device_updated.emit(device_id)
            else:
                retry_seconds = self._register_probe_failure(device.port_name)
                self.diagnostic_event.emit(
                    f"{device.port_name} 无法打开，自动识别将在 {retry_seconds}s 后重试"
                )
                QtCore.QTimer.singleShot(
                    0,
                    lambda: self.remove_device(
                        device_id, preserve_probe_backoff=True
                    ),
                )

    def _on_frame_received(self, frame: SpectrumFrame):
        device = self.devices.get(frame.device_id)
        if not device:
            return
        self._latest_frames[frame.device_id] = frame
        self.frame_arrived.emit(frame)

    def _on_process_tick(self):
        frames = self._latest_frames
        self._latest_frames = {}
        for device_id, frame in frames.items():
            device = self.devices.get(device_id)
            if not device:
                continue
            device.set_latest_data(frame.pixels)
            x = np.arange(len(frame.pixels), dtype=np.float64)
            self.data_arrived.emit(device_id, x, device.latest_pixels.copy())

    def _on_response(self, device_id: int, cmd: int, params: bytes):
        device = self.devices.get(device_id)
        if not device:
            return
        # Firmware 2.2/2.3 acknowledges DEVICE_INIT using response Cmd=0x01
        # instead of the documented 0x10.  A one-byte 0x6x/0x7x payload cannot
        # be a version response, so it is safe to recognize this deviation.
        if (cmd == CmdCode.GET_VERSION and len(params) == 1
                and (params[0] & 0xE0) == 0x60):
            success = not bool(check_status(params[0]))
            if not success:
                self.error_occurred.emit(device_id, "设备初始化返回失败")
            else:
                self.diagnostic_event.emit(
                    f"设备 {device_id} 已接收兼容型初始化 ACK（响应 Cmd=0x01）"
                )
            self.command_completed.emit(
                device_id, int(CmdCode.DEVICE_INIT), success, bytes(params)
            )
            if device_id in self._announced_devices:
                self.device_updated.emit(device_id)
            return
        is_set = cmd == CmdCode.DEVICE_INIT or 0x20 <= cmd <= 0x2C or 0x50 <= cmd <= 0x54
        if is_set:
            response_params = bytes(params)
            if not params:
                if cmd == CmdCode.SET_TRIG_MODE:
                    self._complete_sync_trigger_mode(device_id, False)
                self.error_occurred.emit(device_id, f"命令 0x{cmd:02X} 响应缺少状态")
                self.command_completed.emit(device_id, int(cmd), False, response_params)
                return
            if check_status(params[0]):
                if cmd == CmdCode.SET_TRIG_MODE:
                    self._complete_sync_trigger_mode(device_id, False)
                if cmd in (CmdCode.START_SINGLE, CmdCode.START_CONTINUOUS):
                    self._acquisition_requested.discard(device_id)
                self._pending_updates.pop((device_id, int(cmd)), None)
                self.error_occurred.emit(device_id, f"命令 0x{cmd:02X} 返回失败")
                self.command_completed.emit(device_id, int(cmd), False, response_params)
                return
            apply = self._pending_updates.pop((device_id, int(cmd)), None)
            if apply:
                apply()
            if cmd == CmdCode.SET_TRIG_MODE:
                self._complete_sync_trigger_mode(device_id, True)
            params = params[1:]
            if cmd in (CmdCode.START_SINGLE, CmdCode.START_CONTINUOUS):
                device.acquiring = True
            elif cmd == CmdCode.STOP_ACQUISITION:
                self._acquisition_requested.discard(device_id)
                device.acquiring = False
            self.command_completed.emit(device_id, int(cmd), True, response_params)

        if cmd == CmdCode.GET_VERSION and len(params) >= 32:
            try:
                device.info = self._decode_device_info(params)
            except ValueError as exc:
                if device_id in self._probing:
                    self.diagnostic_event.emit(
                        f"{device.port_name} 返回的设备信息无效：{exc}"
                    )
                else:
                    self.error_occurred.emit(device_id, f"设备信息无效：{exc}")
                return
            device.initialized = True
            device.update_start_wavelength()
            worker = self._workers.get(device_id)
            if worker:
                QtCore.QMetaObject.invokeMethod(
                    worker, "set_pixel_info", QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(int, device.info.pixel_count),
                    QtCore.Q_ARG(int, device.info.start_pixel),
                    QtCore.Q_ARG(int, device.info.valid_pixel),
                )
            if device_id in self._probing:
                self._probing.discard(device_id)
                self._probe_tokens.pop(device_id, None)
                device.connected = True
                port_key = device.port_name.upper()
                self._probe_failures.pop(port_key, None)
                self._probe_retry_after.pop(port_key, None)
                if device_id not in self._announced_devices:
                    self._announced_devices.add(device_id)
                    self.device_added.emit(device_id)
                self.diagnostic_event.emit(
                    f"已识别光谱仪 {device.port_name}："
                    f"{device.info.valid_pixel} 个有效像素"
                )
                self.device_connected.emit(device_id)
        elif cmd == CmdCode.QUERY_CALIB_COEFF and len(params) >= 16:
            c4, c3, c2, c1 = struct.unpack("<4f", params[:16])
            device.wavelength_calib = CalibrationData(c1, c2, c3, c4)
            device.update_start_wavelength()
            self.calib_ready.emit(device_id, c1, c2, c3, c4)
        elif cmd == CmdCode.QUERY_INTEG_TIME and len(params) >= 4:
            device.integration_time_us = struct.unpack("<I", params[:4])[0]
        elif cmd == CmdCode.QUERY_TRIG_MODE and params:
            device.trigger_mode = params[0]
        elif cmd == CmdCode.QUERY_INTERVAL and len(params) >= 4:
            device.interval_us = struct.unpack("<I", params[:4])[0]
        elif cmd == CmdCode.QUERY_AVG_COUNT and len(params) >= 4:
            device.avg_count = struct.unpack("<I", params[:4])[0]
        elif cmd == CmdCode.QUERY_GAIN and params:
            device.gain = params[0]
        elif cmd == CmdCode.QUERY_DELAY and len(params) >= 4:
            device.delay_us = struct.unpack("<I", params[:4])[0]
        elif cmd == CmdCode.QUERY_SERIAL_NUM and len(params) >= 32:
            value = bytes(params[:32]).rstrip(b"\x00").decode("ascii", errors="replace").strip()
            if value and not set(value) <= {"0"}:
                device.info.prod_serial = value
        if device_id in self._announced_devices:
            self.device_updated.emit(device_id)

    def _on_error(self, device_id: int, message: str):
        self.error_occurred.emit(device_id, message)

    def _on_disconnect(self, device_id: int):
        self._acquisition_requested.discard(device_id)
        device = self.devices.get(device_id)
        if device:
            self._probing.discard(device_id)
            self._probe_tokens.pop(device_id, None)
            device.connected = False
            device.initialized = False
            device.acquiring = False
            if device_id in self._announced_devices:
                self.device_updated.emit(device_id)
                self._schedule_reconnect(device_id)
            else:
                retry_seconds = self._register_probe_failure(device.port_name)
                self.diagnostic_event.emit(
                    f"{device.port_name} 在协议识别期间断开，"
                    f"将在 {retry_seconds}s 后重试"
                )
                QtCore.QTimer.singleShot(
                    0,
                    lambda: self.remove_device(
                        device_id, preserve_probe_backoff=True
                    ),
                )

    def _on_probe_timeout(self, device_id: int, token: int):
        if self._probe_tokens.get(device_id) != token:
            return
        device = self.devices.get(device_id)
        if not device or device_id not in self._probing:
            return
        retry_seconds = self._register_probe_failure(device.port_name)
        self.diagnostic_event.emit(
            f"{device.port_name} 未通过光谱仪协议握手，已忽略；"
            f"自动识别将在 {retry_seconds}s 后重试"
        )
        self.remove_device(device_id, preserve_probe_backoff=True)

    def _register_probe_failure(self, port_name: str) -> int:
        port_key = port_name.upper()
        failures = self._probe_failures.get(port_key, 0) + 1
        self._probe_failures[port_key] = failures
        retry_seconds = min(300, 30 * (2 ** min(failures - 1, 4)))
        self._probe_retry_after[port_key] = time.monotonic() + retry_seconds
        return retry_seconds

    @staticmethod
    def _decode_device_info(params: bytes) -> DeviceInfo:
        if len(params) < 32:
            raise ValueError("版本响应少于 32 字节")
        values = list(struct.unpack("<IIffIIII", params[:32]))
        values.extend(
            struct.unpack("<II", params[32:40]) if len(params) >= 40 else (0, 0)
        )
        pixel_count = values[5]
        start_pixel = values[6]
        valid_pixel = values[7]
        if not math.isfinite(values[2]) or not math.isfinite(values[3]):
            raise ValueError("硬件或固件版本号不是有效数值")
        if pixel_count <= 0 or pixel_count > 65_536:
            raise ValueError(f"像素总数异常（{pixel_count}）")
        if valid_pixel <= 0:
            raise ValueError("有效像素数必须大于 0")
        if start_pixel + valid_pixel > pixel_count:
            raise ValueError(
                f"有效像素范围 {start_pixel}+{valid_pixel} 超出总像素 {pixel_count}"
            )
        return DeviceInfo(
            name=values[0],
            dev_type=values[1],
            hw_ver=values[2],
            fw_ver=values[3],
            serial_num=values[4],
            pixel_count=pixel_count,
            start_pixel=start_pixel,
            valid_pixel=valid_pixel,
            pos_time_min=values[8],
            pos_time_max=values[9],
        )

    def _schedule_reconnect(self, device_id: int, immediate: bool = False):
        if device_id not in self.devices or device_id in self._removing:
            return
        attempt = self._reconnect_attempts.get(device_id, 0) + 1
        self._reconnect_attempts[device_id] = attempt
        delay_ms = 0 if immediate else min(30_000, 1000 * (2 ** min(attempt - 1, 5)))
        self.diagnostic_event.emit(
            f"设备 {device_id} 将在 {delay_ms / 1000:.1f}s 后进行第 {attempt} 次重连"
        )
        QtCore.QTimer.singleShot(delay_ms, lambda: self._reconnect_now(device_id))

    def _reconnect_now(self, device_id: int):
        if device_id not in self.devices or device_id in self._removing:
            return
        worker = self._workers.get(device_id)
        if worker:
            QtCore.QMetaObject.invokeMethod(worker, "do_connect", QtCore.Qt.QueuedConnection)

    @staticmethod
    def _pack(fmt: str, *args) -> bytes:
        return struct.pack(fmt, *args)
