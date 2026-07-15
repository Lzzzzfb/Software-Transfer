"""可测试的光谱处理管线。"""

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from ..domain.enums import ProcessingMode
from .formula import evaluate_formula


@dataclass(frozen=True)
class ProcessingConfig:
    mode: ProcessingMode = ProcessingMode.RAW
    custom_formula: str = ""
    baseline_enabled: bool = False
    baseline_lam: float = 1e5
    baseline_order: int = 2
    baseline_max_iter: int = 15


@dataclass(frozen=True)
class ProcessedSpectrum:
    wavelengths: np.ndarray
    raw: np.ndarray
    calibrated: np.ndarray
    values: np.ndarray
    baseline: Optional[np.ndarray] = None
    warnings: Tuple[str, ...] = ()


class SpectrumProcessor:
    def process(
        self,
        wavelengths,
        raw,
        config: ProcessingConfig = ProcessingConfig(),
        *,
        intensity_calibration=None,
        background=None,
        reference=None,
    ) -> ProcessedSpectrum:
        x = self._array("wavelengths", wavelengths)
        raw_array = self._array("raw", raw)
        self._same_shape(x, raw_array)

        calibrated = raw_array.copy()
        if intensity_calibration is not None:
            calibration = self._array("intensity_calibration", intensity_calibration)
            self._same_shape(calibrated, calibration)
            calibrated *= calibration

        background_array = self._optional_array(background, calibrated, "background")
        reference_array = self._optional_array(reference, calibrated, "reference")
        warnings = []

        if config.mode == ProcessingMode.RAW:
            values = calibrated.copy()
        elif config.mode == ProcessingMode.DARK_SUBTRACT:
            if background_array is None:
                raise ValueError("扣背景模式需要背景光谱")
            values = calibrated - background_array
        elif config.mode == ProcessingMode.ABSORBANCE:
            if background_array is None or reference_array is None:
                raise ValueError("吸光度模式需要背景光谱和参考光谱")
            numerator = calibrated - background_array
            denominator = reference_array - background_array
            invalid = (denominator == 0) | (numerator <= 0)
            ratio = np.divide(
                numerator,
                denominator,
                out=np.ones_like(numerator),
                where=denominator != 0,
            )
            ratio = np.clip(ratio, 1e-12, None)
            values = -np.log10(ratio)
            if np.any(invalid):
                warnings.append(f"{int(np.count_nonzero(invalid))} 个像素超出吸光度有效域")
        elif config.mode == ProcessingMode.CUSTOM:
            if not config.custom_formula:
                raise ValueError("自定义模式需要公式")
            dark = background_array if background_array is not None else np.zeros_like(calibrated)
            ref = reference_array if reference_array is not None else np.ones_like(calibrated)
            values = evaluate_formula(
                config.custom_formula,
                {"I": calibrated, "Idark": dark, "Ib": dark, "I0": ref, "x": x},
            )
        else:
            raise ValueError(f"未知处理模式: {config.mode}")

        baseline = None
        if config.baseline_enabled and config.mode != ProcessingMode.ABSORBANCE:
            from ..extensions.airpls import airpls

            baseline = airpls(
                values,
                lam=config.baseline_lam,
                order=config.baseline_order,
                max_iter=config.baseline_max_iter,
            )
            values = values - baseline

        if not np.all(np.isfinite(values)):
            raise ValueError("处理结果包含无穷大或无效值")
        return ProcessedSpectrum(
            wavelengths=x.copy(),
            raw=raw_array.copy(),
            calibrated=calibrated.copy(),
            values=values,
            baseline=None if baseline is None else np.asarray(baseline).copy(),
            warnings=tuple(warnings),
        )

    @staticmethod
    def _array(name, value):
        result = np.asarray(value, dtype=np.float64)
        if result.ndim != 1:
            raise ValueError(f"{name} 必须是一维数组")
        return result

    @classmethod
    def _optional_array(cls, value, target, name):
        if value is None:
            return None
        result = cls._array(name, value)
        cls._same_shape(target, result)
        return result

    @staticmethod
    def _same_shape(first, second):
        if first.shape != second.shape:
            raise ValueError("光谱数组长度不一致")
