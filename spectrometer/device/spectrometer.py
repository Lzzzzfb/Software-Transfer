"""
光谱仪设备模型 — 封装单台光谱仪的全部状态与参数。
"""

import time
from dataclasses import dataclass, field
from typing import Optional, List
import numpy as np


@dataclass
class DeviceInfo:
    """设备版本/标识信息"""
    name: int = 0           # U32 设备名称代号
    dev_type: int = 0       # U32 设备类型
    hw_ver: float = 0.0     # f32 硬件版本
    fw_ver: float = 0.0     # f32 固件版本
    serial_num: int = 0     # U32 序列号
    pixel_count: int = 0    # U32 总像素数
    start_pixel: int = 0    # U32 起始像素
    valid_pixel: int = 0    # U32 有效像素数
    pos_time_min: int = 0   # U32 曝光时间最小值(us)
    pos_time_max: int = 0   # U32 曝光时间最大值(us)
    prod_serial: str = ''   # 生产序列号 ASCII 字符串 (0x3C 查询)
    sensor_type: str = ''   # 传感器类型描述


@dataclass
class CalibrationData:
    """波长校准参数 — 三阶多项式"""
    c1: float = 0.0  # 0阶系数
    c2: float = 1.0  # 1阶系数
    c3: float = 0.0  # 2阶系数
    c4: float = 0.0  # 3阶系数

    def calc_wavelength(self, pixel_index: int) -> float:
        """根据像素索引计算波长 (3阶多项式)"""
        x = float(pixel_index)
        return self.c1 + self.c2 * x + self.c3 * x * x + self.c4 * x * x * x

    def calc_wavelengths(self, pixel_indices: np.ndarray) -> np.ndarray:
        """批量计算波长数组"""
        x = pixel_indices.astype(np.float64)
        return self.c1 + self.c2 * x + self.c3 * x**2 + self.c4 * x**3


class SpectrometerDevice:
    """单台光谱仪设备模型"""

    def __init__(self, device_id: int, port_name: str = ''):
        self.device_id = device_id          # 设备编号 (用户可见)
        self.port_name = port_name           # 串口号
        self.connected = False
        self.initialized = False

        # 设备信息
        self.info = DeviceInfo()

        # 校准数据
        self.wavelength_calib = CalibrationData()
        self.intensity_calib: Optional[np.ndarray] = None  # 强度校准系数数组

        # 采集参数
        self.integration_time_us: int = 10000   # 积分时间(us), 默认10ms
        self.trigger_mode: int = 0              # 触发模式: 0=软件, 1=外部, 2=软件主机
        self.interval_us: int = 0               # 采集间隔(us)
        self.avg_count: int = 1                 # 平均次数
        self.dark_current_enabled: bool = False # 暗电流去除
        self.gain: int = 0                      # 增益 (0-63)
        self.delay_us: int = 0                  # 触发延时(us)

        # 同步参数
        self.is_master: bool = False            # 是否为同步主机
        self.sync_enabled: bool = False         # 是否启用同步
        self.sync_delay_us: int = 0             # 与主机的同步延时

        # 光谱数据
        self.reference_spectrum: Optional[np.ndarray] = None  # 参考光谱 I0
        self.background_spectrum: Optional[np.ndarray] = None # 背景光谱 Idark
        self.latest_pixels: Optional[np.ndarray] = None       # 最新原始光谱数据
        self.latest_wavelengths: Optional[np.ndarray] = None  # 最新波长数组

        # 基线校正
        self.baseline_enabled: bool = False       # 是否启用基线校正
        self.baseline_lam: float = 1e5            # airPLS 平滑度参数
        self.baseline_order: int = 2              # 差分阶数
        self.baseline_max_iter: int = 15          # 最大迭代次数
        self.baseline_y: Optional[np.ndarray] = None         # 最近一次估计的基线

        # 外触发
        self.external_trigger_enabled: bool = False
        self.ext_trig_delay_us: int = 0

        # 同步信号输出
        self.sync_output_enabled: bool = False
        self.sync_output_delay_us: int = 0

        # 运行状态
        self.enabled: bool = True    # 卡片启用/停用
        self.acquiring: bool = False

        # 起始波长 (用于排序)
        self.start_wavelength: float = 0.0

    def update_start_wavelength(self):
        """根据校准参数更新起始波长"""
        if self.info.start_pixel < self.info.valid_pixel:
            self.start_wavelength = self.wavelength_calib.calc_wavelength(self.info.start_pixel)

    def get_wavelength_array(self, n_pixels: int = 0) -> np.ndarray:
        """获取当前设备的波长数组。n_pixels 为0时使用 valid_pixel，若仍为0则使用默认2048。"""
        n = n_pixels or self.info.valid_pixel or 2048
        indices = np.arange(self.info.start_pixel, self.info.start_pixel + n,
                            dtype=np.float64)
        return self.wavelength_calib.calc_wavelengths(indices)

    def set_latest_data(self, pixels: list):
        """存储最新光谱数据"""
        raw = np.array(pixels, dtype=np.float64)
        # 应用强度校准
        if self.intensity_calib is not None and len(self.intensity_calib) == len(raw):
            raw = raw * self.intensity_calib
        self.latest_pixels = raw
        # 波长始终按实际数据长度生成，确保 x/y 一对一匹配
        self.latest_wavelengths = self.get_wavelength_array(len(raw))

    def get_display_spectrum(self, mode: str = 'raw',
                              custom_formula: str = '') -> Optional[tuple]:
        """
        根据显示模式返回 (x, y) 数据。

        mode: 'raw' | 'dark_subtract' | 'absorbance' | 'custom'
        若 baseline_enabled=True, 在显示模式处理后扣除基线.
        """
        if self.latest_pixels is None or self.latest_wavelengths is None:
            return None

        x = self.latest_wavelengths
        y = self.latest_pixels.copy()

        if mode == 'dark_subtract':
            if self.background_spectrum is not None:
                bg = self._match_length(self.background_spectrum, len(y))
                y = y - bg
            else:
                return None
        elif mode == 'absorbance':
            if self.background_spectrum is not None and self.reference_spectrum is not None:
                bg = self._match_length(self.background_spectrum, len(y))
                ref = self._match_length(self.reference_spectrum, len(y))
                # A = -log10((I - Idark) / (I0 - Idark))
                numerator = y - bg
                denominator = ref - bg
                with np.errstate(divide='ignore', invalid='ignore'):
                    ratio = np.where(denominator != 0, numerator / denominator, 1.0)
                    ratio = np.clip(ratio, 1e-10, None)
                    y = -np.log10(ratio)
            else:
                return None
        elif mode == 'custom' and custom_formula:
            y = self._eval_custom_formula(custom_formula, x, y)

        # airPLS 基线校正 (在各显示模式之后应用)
        if self.baseline_enabled and mode != 'absorbance':
            self.baseline_y, y = self._apply_baseline_correction(y)

        return x, y

    def _apply_baseline_correction(self, y: np.ndarray) -> tuple:
        """执行 airPLS 基线校正, 返回 (baseline, corrected)."""
        from ..extensions.airpls import airpls
        baseline = airpls(y, lam=self.baseline_lam, order=self.baseline_order,
                          max_iter=self.baseline_max_iter)
        return baseline, y - baseline

    def _eval_custom_formula(self, formula: str, x: np.ndarray, I: np.ndarray) -> np.ndarray:
        """计算用户自定义公式"""
        Ib = self._match_length(self.background_spectrum, len(I)) if self.background_spectrum is not None else np.zeros_like(I)
        I0 = self._match_length(self.reference_spectrum, len(I)) if self.reference_spectrum is not None else np.ones_like(I)
        context = {'I': I, 'Ib': Ib, 'I0': I0, 'x': x,
                    'np': np, 'log10': np.log10, 'log': np.log, 'loge': np.log,
                    'abs': np.abs, 'sqrt': np.sqrt}
        try:
            result = eval(formula, {"__builtins__": {}}, context)
            return np.array(result, dtype=np.float64)
        except Exception:
            return I

    @staticmethod
    def _match_length(arr: np.ndarray, target_len: int) -> np.ndarray:
        """将校准数组匹配到目标长度"""
        if arr is None:
            return None
        if len(arr) == target_len:
            return arr.copy()
        if len(arr) > target_len:
            return arr[:target_len].copy()
        return np.pad(arr, (0, target_len - len(arr)), 'edge')

    def to_dict(self) -> dict:
        """导出设备参数为字典（用于保存）"""
        return {
            'device_id': self.device_id,
            'port_name': self.port_name,
            'integration_time_us': self.integration_time_us,
            'trigger_mode': self.trigger_mode,
            'interval_us': self.interval_us,
            'avg_count': self.avg_count,
            'gain': self.gain,
            'delay_us': self.delay_us,
            'sync_enabled': self.sync_enabled,
            'is_master': self.is_master,
            'calib_c1': self.wavelength_calib.c1,
            'calib_c2': self.wavelength_calib.c2,
            'calib_c3': self.wavelength_calib.c3,
            'calib_c4': self.wavelength_calib.c4,
        }
