import numpy as np
import pytest

from spectrometer.domain.enums import ProcessingMode
from spectrometer.processing.processor import ProcessingConfig, SpectrumProcessor


def test_raw_calibration_dark_subtract_and_absorbance_are_separate():
    processor = SpectrumProcessor()
    x = [400, 401]
    raw = [10, 20]
    calibrated = processor.process(x, raw, intensity_calibration=[2, 3])
    np.testing.assert_array_equal(calibrated.raw, [10, 20])
    np.testing.assert_array_equal(calibrated.calibrated, [20, 60])
    np.testing.assert_array_equal(calibrated.values, [20, 60])

    dark = processor.process(
        x,
        raw,
        ProcessingConfig(mode=ProcessingMode.DARK_SUBTRACT),
        background=[1, 2],
    )
    np.testing.assert_array_equal(dark.values, [9, 18])

    absorbance = processor.process(
        x,
        raw,
        ProcessingConfig(mode=ProcessingMode.ABSORBANCE),
        background=[0, 0],
        reference=[100, 200],
    )
    np.testing.assert_allclose(absorbance.values, [1, 1])


def test_processor_rejects_silent_length_matching():
    with pytest.raises(ValueError, match="长度不一致"):
        SpectrumProcessor().process([1, 2], [1], background=[0])


def test_custom_formula_uses_reference_aliases():
    result = SpectrumProcessor().process(
        [1, 2],
        [5, 10],
        ProcessingConfig(mode=ProcessingMode.CUSTOM, custom_formula="(I-Ib)/(I0-Idark)"),
        background=[1, 2],
        reference=[9, 18],
    )
    np.testing.assert_allclose(result.values, [0.5, 0.5])
