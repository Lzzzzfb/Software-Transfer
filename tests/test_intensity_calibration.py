import math

import pytest

from spectrometer.processing.intensity_calibration import (
    IntensityCalibrationError,
    parse_intensity_calibration_bytes,
)


def parse(content, pixel_count=3):
    return parse_intensity_calibration_bytes(
        content,
        device_serial="003",
        pixel_count=pixel_count,
        source_name="coefficients.csv",
    )


def test_imports_single_column_with_header_comments_and_utf8_bom():
    calibration = parse(
        "\ufeff# factory calibration\nCoefficient\n1.0\n0.5\n2.25\n".encode(
            "utf-8"
        )
    )

    assert calibration.device_serial == "003"
    assert calibration.pixel_count == 3
    assert calibration.coefficients == (1.0, 0.5, 2.25)
    assert calibration.source_name == "coefficients.csv"
    assert len(calibration.source_sha256) == 64
    assert len(calibration.calibration_id) == 16


@pytest.mark.parametrize(
    "content",
    [
        b"Pixel,Coefficient\n0,1.0\n1,0.5\n2,2.25\n",
        b"Pixel\tCoefficient\n0\t1.0\n1\t0.5\n2\t2.25\n",
        b"Pixel Coefficient\n0 1.0\n1 0.5\n2 2.25\n",
    ],
)
def test_imports_two_column_pixel_coefficient_formats(content):
    assert parse(content).coefficients == (1.0, 0.5, 2.25)


def test_imports_gb18030_header():
    calibration = parse("像素,校准系数\n0,1\n1,2\n2,3\n".encode("gb18030"))

    assert calibration.coefficients == (1.0, 2.0, 3.0)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"1\n2\n", "系数数量"),
        (b"1\n2\n3\n4\n", "系数数量"),
        (b"Pixel,Coefficient\n0,1\n2,2\n1,3\n", "连续递增"),
        (b"Pixel,Coefficient\n0,1\n1,2\n1,3\n", "连续递增"),
        (b"1\n0\n2\n", "有限正数"),
        (b"1\n-1\n2\n", "有限正数"),
        (b"1\nnan\n2\n", "有限正数"),
        (b"1\ninf\n2\n", "有限正数"),
        (b"1,2,3\n4,5,6\n7,8,9\n", "一列或两列"),
        (b"Coefficient\n1\nbad\n2\n", "无法解析"),
    ],
)
def test_rejects_malformed_or_incompatible_input(content, message):
    with pytest.raises(IntensityCalibrationError, match=message):
        parse(content)


def test_rejects_unsupported_text_encoding():
    with pytest.raises(IntensityCalibrationError, match="编码"):
        parse(b"\x81")


def test_rejects_invalid_target_identity():
    with pytest.raises(IntensityCalibrationError, match="有效像素"):
        parse_intensity_calibration_bytes(
            b"1",
            device_serial="003",
            pixel_count=0,
            source_name="coeff.txt",
        )


def test_coefficients_are_finite_positive_numbers():
    calibration = parse(b"1e-6\n1.5\n1e6\n")

    assert all(math.isfinite(value) and value > 0 for value in calibration.coefficients)
