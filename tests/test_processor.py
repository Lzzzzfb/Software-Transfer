import numpy as np
import pytest

from spectrometer.domain.enums import ProcessingMode
from spectrometer.domain.models import SpectrumFrame
from spectrometer.processing.processor import (
    ProcessingConfig,
    ProcessingSnapshot,
    SpectrumProcessor,
)


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


def test_process_frame_preserves_negative_background_subtraction():
    snapshot = ProcessingSnapshot(
        mode="dark_subtract",
        wavelengths=(500.0, 501.0),
        background=(15.0, 25.0),
    )
    frame = SpectrumFrame.create(0, 9 << 8, [10, 30])

    processed = SpectrumProcessor().process_frame(frame, snapshot)

    assert processed.sequence == 9
    assert processed.values == (-5.0, 5.0)


def test_intensity_calibration_applies_to_current_background_and_reference():
    processor = SpectrumProcessor()
    x = [400, 401]
    coefficients = [2, 3]

    dark = processor.process(
        x,
        [10, 20],
        ProcessingConfig(mode=ProcessingMode.DARK_SUBTRACT),
        intensity_calibration=coefficients,
        background=[1, 2],
    )
    absorbance = processor.process(
        x,
        [10, 20],
        ProcessingConfig(mode=ProcessingMode.ABSORBANCE),
        intensity_calibration=coefficients,
        background=[1, 2],
        reference=[100, 200],
    )

    np.testing.assert_array_equal(dark.values, [18, 54])
    np.testing.assert_allclose(
        absorbance.values,
        [-np.log10(18 / 198), -np.log10(54 / 594)],
    )


def test_processing_snapshot_carries_airpls_and_calibration_metadata():
    snapshot = ProcessingSnapshot(
        mode="absorbance",
        wavelengths=(500.0, 501.0),
        intensity_calibration=(1.0, 1.0),
        intensity_calibration_id="cal-003",
        baseline_enabled=True,
        baseline_lam=2e5,
        baseline_order=3,
        baseline_max_iter=22,
        processing_pipeline_version=2,
    )

    assert snapshot.config.baseline_enabled
    assert snapshot.config.baseline_lam == 2e5
    assert snapshot.config.baseline_order == 3
    assert snapshot.config.baseline_max_iter == 22
    assert snapshot.intensity_calibration_id == "cal-003"
    assert snapshot.processing_pipeline_version == 2


def test_airpls_runs_after_absorbance(monkeypatch):
    observed = []

    def fake_airpls(values, **kwargs):
        observed.append(np.asarray(values).copy())
        return np.asarray([0.25, 0.5])

    from importlib import import_module

    airpls_module = import_module("spectrometer.extensions.airpls")
    monkeypatch.setattr(airpls_module, "airpls", fake_airpls)
    config = ProcessingConfig(
        mode=ProcessingMode.ABSORBANCE,
        baseline_enabled=True,
        baseline_lam=3e5,
        baseline_order=2,
        baseline_max_iter=9,
    )

    result = SpectrumProcessor().process(
        [400, 401],
        [10, 20],
        config,
        background=[0, 0],
        reference=[100, 200],
    )

    np.testing.assert_allclose(observed[0], [1, 1])
    np.testing.assert_allclose(result.baseline, [0.25, 0.5])
    np.testing.assert_allclose(result.values, [0.75, 0.5])
