from dataclasses import replace
import json

import pytest

from spectrometer.processing.calibration_repository import (
    CalibrationRepositoryError,
    IntensityCalibrationRepository,
)
from spectrometer.processing.intensity_calibration import (
    parse_intensity_calibration_bytes,
)


def calibration(serial="003", count=3, values=b"1\n2\n3\n"):
    return parse_intensity_calibration_bytes(
        values,
        device_serial=serial,
        pixel_count=count,
        source_name="factory.csv",
    )


def test_repository_saves_loads_and_clears_by_serial_and_pixel_count(tmp_path):
    repository = IntensityCalibrationRepository(tmp_path)
    item = calibration()

    path = repository.save(item)

    assert path.exists()
    assert repository.load("003", 3) == item
    assert repository.load("003", 4) is None
    assert repository.load("004", 3) is None
    assert repository.clear("003", 3)
    assert repository.load("003", 3) is None
    assert not repository.clear("003", 3)


def test_repository_replaces_existing_record_atomically(tmp_path):
    repository = IntensityCalibrationRepository(tmp_path)
    first = calibration()
    second = calibration(values=b"4\n5\n6\n")

    path = repository.save(first)
    repository.save(second)

    assert repository.load("003", 3) == second
    assert not path.with_suffix(".json.tmp").exists()


def test_repository_rejects_persistent_record_without_serial(tmp_path):
    repository = IntensityCalibrationRepository(tmp_path)

    with pytest.raises(CalibrationRepositoryError, match="生产序列号"):
        repository.save(calibration(serial=""))


def test_repository_rejects_corrupt_or_mismatched_record(tmp_path):
    repository = IntensityCalibrationRepository(tmp_path)
    path = repository.save(calibration())
    data = json.loads(path.read_text(encoding="utf-8"))
    data["coefficients"] = [1, 2]
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(CalibrationRepositoryError, match="系数数量"):
        repository.load("003", 3)


def test_failed_replace_keeps_previous_record(tmp_path, monkeypatch):
    repository = IntensityCalibrationRepository(tmp_path)
    first = calibration()
    repository.save(first)

    original_replace = type(repository.path_for("003", 3)).replace

    def fail_replace(path, target):
        if str(path).endswith(".json.tmp"):
            raise OSError("injected replace failure")
        return original_replace(path, target)

    monkeypatch.setattr(type(repository.path_for("003", 3)), "replace", fail_replace)

    with pytest.raises(CalibrationRepositoryError, match="保存"):
        repository.save(calibration(values=b"4\n5\n6\n"))

    assert repository.load("003", 3) == first
    assert not repository.path_for("003", 3).with_suffix(".json.tmp").exists()


def test_repository_validates_record_identity(tmp_path):
    repository = IntensityCalibrationRepository(tmp_path)
    item = calibration()

    with pytest.raises(CalibrationRepositoryError, match="像素数"):
        repository.save(replace(item, pixel_count=4))
