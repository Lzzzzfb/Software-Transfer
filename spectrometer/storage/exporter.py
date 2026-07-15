"""
数据存储导出模块 — CSV/TXT格式光谱数据存储。
"""

import os
import time
import csv
from datetime import datetime
from typing import Optional, List
import numpy as np

from PyQt5.QtCore import QObject, pyqtSignal


class DataExporter(QObject):
    """数据导出器"""

    export_complete = pyqtSignal(str)       # file_path
    export_error = pyqtSignal(str)          # error_msg

    def __init__(self, device_manager):
        super().__init__()
        self.dm = device_manager
        self.default_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         '..', '..', 'data')
        self._ensure_dir(self.default_path)

    def set_default_path(self, path: str):
        self.default_path = path
        self._ensure_dir(path)

    def export_spectrum(self, device, file_path: Optional[str] = None,
                        note: str = '') -> str:
        """导出单通道光谱数据"""
        if device.latest_wavelengths is None or device.latest_pixels is None:
            self.export_error.emit("无数据可导出")
            return ''

        if file_path is None:
            file_path = self._generate_filename(device, note)

        try:
            self._write_csv(file_path, device)
            self.export_complete.emit(file_path)
            return file_path
        except Exception as e:
            self.export_error.emit(str(e))
            return ''

    def export_multi_device(self, devices: list, file_path: Optional[str] = None,
                            merge: bool = False, note: str = '') -> List[str]:
        """导出多通道数据"""
        saved = []
        if merge:
            # 合并存储到一个文件
            if file_path is None:
                file_path = os.path.join(
                    self.default_path,
                    f"merged_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
            try:
                self._write_merged_csv(file_path, devices)
                saved.append(file_path)
                self.export_complete.emit(file_path)
            except Exception as e:
                self.export_error.emit(str(e))
        else:
            for device in devices:
                fp = self.export_spectrum(device, note=note)
                if fp:
                    saved.append(fp)
        return saved

    def export_background(self, device) -> Optional[str]:
        """存储背景光谱（自动命名，不覆盖）"""
        if device.background_spectrum is None:
            return None
        filename = self._unique_filename(
            f"background_ch{device.device_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        file_path = os.path.join(self.default_path, filename)
        try:
            self._write_single_array(file_path, device.get_wavelength_array(),
                                     device.background_spectrum, device, 'Background')
            self.export_complete.emit(file_path)
            return file_path
        except Exception as e:
            self.export_error.emit(str(e))
            return None

    def export_reference(self, device) -> Optional[str]:
        """存储参考光谱"""
        if device.reference_spectrum is None:
            return None
        filename = self._unique_filename(
            f"reference_ch{device.device_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        file_path = os.path.join(self.default_path, filename)
        try:
            self._write_single_array(file_path, device.get_wavelength_array(),
                                     device.reference_spectrum, device, 'Reference')
            self.export_complete.emit(file_path)
            return file_path
        except Exception as e:
            self.export_error.emit(str(e))
            return None

    def _generate_filename(self, device, note: str = '') -> str:
        """生成文件名: 采集时间+曝光时间+可选备注"""
        now = datetime.now().strftime('%Y%m%d_%H%M%S')
        exp_ms = device.integration_time_us / 1000
        trig = 'ext' if device.external_trigger_enabled else 'int'
        parts = [now, f"{exp_ms:.0f}ms", trig, f"ch{device.device_id}"]
        if note:
            parts.append(note)
        name = '_'.join(parts) + '.csv'
        return os.path.join(self.default_path, name)

    def _unique_filename(self, base_name: str) -> str:
        """生成不冲突的文件名"""
        name, ext = os.path.splitext(base_name)
        if not ext:
            ext = '.csv'
        counter = 0
        while True:
            if counter == 0:
                candidate = f"{name}{ext}"
            else:
                candidate = f"{name}_{counter}{ext}"
            if not os.path.exists(os.path.join(self.default_path, candidate)):
                return candidate
            counter += 1

    def _write_csv(self, file_path: str, device):
        """写入CSV文件: 头信息 + 两列数据(波长, 强度)"""
        with open(file_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            # 头信息
            writer.writerow(['# ZGCAI Spectrometer Data'])
            writer.writerow(['# Device ID', device.device_id])
            writer.writerow(['# Serial Number', device.info.serial_num])
            writer.writerow(['# Integration Time (us)', device.integration_time_us])
            writer.writerow(['# Average Count', device.avg_count])
            writer.writerow(['# External Trigger', 'Yes' if device.external_trigger_enabled else 'No'])
            writer.writerow(['# Trigger Delay (us)', device.delay_us])
            writer.writerow(['# Spectrum Range',
                             f'{device.start_wavelength:.3f} - '
                             f'{device.wavelength_calib.calc_wavelength(device.info.start_pixel + device.info.valid_pixel - 1):.3f}'])
            writer.writerow(['# Calibration Coefficients',
                             f'C1={device.wavelength_calib.c1}, C2={device.wavelength_calib.c2}, '
                             f'C3={device.wavelength_calib.c3}, C4={device.wavelength_calib.c4}'])
            writer.writerow(['# Export Time', datetime.now().isoformat()])
            writer.writerow([])
            # 数据
            writer.writerow(['Wavelength', 'Intensity'])
            for wl, intensity in zip(device.latest_wavelengths, device.latest_pixels):
                writer.writerow([f'{wl:.4f}', f'{intensity:.4f}'])

    def _write_merged_csv(self, file_path: str, devices: list):
        """写入合并的CSV文件: 所有通道的数据并列"""
        with open(file_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['# ZGCAI Spectrometer Data (Merged)'])
            writer.writerow(['# Channel Count', len(devices)])
            for d in devices:
                writer.writerow([f'# Ch{d.device_id} Integration Time (us)', d.integration_time_us])
            writer.writerow([])

            # 列头: Wavelength, Ch0_Intensity, Ch1_Intensity, ...
            header = ['Wavelength']
            for d in devices:
                header.append(f'Ch{d.device_id}_Intensity')
            writer.writerow(header)

            # 数据行
            max_len = max(len(d.latest_wavelengths) if d.latest_wavelengths is not None else 0
                         for d in devices)
            for i in range(max_len):
                row = []
                # 波长列
                if devices[0].latest_wavelengths is not None and i < len(devices[0].latest_wavelengths):
                    row.append(f'{devices[0].latest_wavelengths[i]:.4f}')
                else:
                    row.append('')
                for d in devices:
                    if d.latest_pixels is not None and i < len(d.latest_pixels):
                        row.append(f'{d.latest_pixels[i]:.4f}')
                    else:
                        row.append('')
                writer.writerow(row)

    def _write_single_array(self, file_path: str, wavelengths: np.ndarray,
                            intensities: np.ndarray, device, data_type: str):
        with open(file_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([f'# {data_type} Spectrum'])
            writer.writerow(['# Device ID', device.device_id])
            writer.writerow(['# Record Time', datetime.now().isoformat()])
            writer.writerow([])
            writer.writerow(['Wavelength', 'Intensity'])
            for wl, it in zip(wavelengths, intensities):
                writer.writerow([f'{wl:.4f}', f'{it:.4f}'])

    def export_buffered(self, device_manager, frames_by_device: dict,
                        save_dir: str = '') -> int:
        """
        导出缓冲区全部数据。
        frames_by_device: {device_id: ([wl_arrays], [px_arrays])}
        每个设备一个文件，每列一帧数据。
        """
        saved = 0
        for device_id, (wl_list, px_list) in frames_by_device.items():
            device = device_manager.get_device(device_id)
            if not device or not px_list:
                continue
            now = datetime.now().strftime('%Y%m%d_%H%M%S')
            exp_ms = device.integration_time_us / 1000
            label = device.port_name.replace(':', '') if device.port_name else f'dev{device_id}'
            file_path = os.path.join(save_dir or self.default_path,
                                     f'{now}_{label}_{exp_ms:.0f}ms_{len(px_list)}frames.csv')
            self._ensure_dir(os.path.dirname(file_path) or save_dir or self.default_path)
            try:
                self._write_multicolumn(file_path, device, wl_list[0] if wl_list else None, px_list)
                self.export_complete.emit(file_path)
                saved += 1
            except Exception as e:
                self.export_error.emit(str(e))
        return saved

    def _write_multicolumn(self, file_path: str, device,
                           wavelengths: 'np.ndarray | None',
                           frames: list):
        """多帧CSV: 第一列波长/像素序号, 后续每列一帧强度"""
        import numpy as np
        with open(file_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['# ZGCAI Spectrometer Data'])
            writer.writerow(['# Device', device.port_name])
            writer.writerow(['# Integration Time (us)', device.integration_time_us])
            writer.writerow(['# Frames', len(frames)])
            writer.writerow(['# Trigger Mode', ['Software', 'SoftMaster', 'External'][device.trigger_mode]])
            writer.writerow(['# Export Time', datetime.now().isoformat()])
            if device.info.prod_serial:
                writer.writerow(['# Serial', device.info.prod_serial])
            writer.writerow([])

            # 确定列数: 取最长帧
            max_len = max(len(f) for f in frames)

            # 表头
            header = ['Pixel']
            for i in range(len(frames)):
                header.append(f'Frame_{i+1}')
            writer.writerow(header)

            # 数据行
            for row_i in range(max_len):
                row = [row_i]  # 像素序号
                for f in frames:
                    row.append(f'{f[row_i]:.4f}' if row_i < len(f) else '')
                writer.writerow(row)

    @staticmethod
    def _ensure_dir(path: str):
        os.makedirs(path, exist_ok=True)
