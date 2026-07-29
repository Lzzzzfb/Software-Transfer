import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

from spectrometer.processing.intensity_calibration import (
    parse_intensity_calibration_bytes,
)
from spectrometer.processing.profiles import DeviceAirplsOverride
from spectrometer.qt import QtWidgets
from spectrometer.ui.main_window import MainWindow


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return _APPLICATION


def test_main_window_auto_loads_calibration_and_device_airpls_profile(tmp_path):
    app = application()
    window = MainWindow(
        simulation=True,
        auto_start_simulation=False,
        settings_path=tmp_path / "settings.json",
    )
    device = window.device_manager.get_device(0)
    serial = device.info.prod_serial
    pixels = device.info.valid_pixel
    calibration = parse_intensity_calibration_bytes(
        ("\n".join(["1"] * pixels)).encode("utf-8"),
        device_serial=serial,
        pixel_count=pixels,
        source_name="flat.txt",
    )
    override = DeviceAirplsOverride(
        follow_global=False,
        enabled=True,
        lam=4e5,
        order=3,
        max_iter=18,
    )
    window.intensity_calibration_repository.save(calibration)
    window.processing_profile_repository.save(serial, override)
    device.intensity_calibration_record = None
    device.intensity_calib = None
    device.airpls_override = None
    window._processing_profile_identity.pop(device.device_id, None)

    window._load_device_processing_configuration(device.device_id)

    assert device.intensity_calibration_record == calibration
    np.testing.assert_allclose(device.intensity_calib, np.ones(pixels))
    assert device.airpls_override == override
    snapshot = window._processing_snapshots([device])[device.device_id]
    assert snapshot.intensity_calibration_id == calibration.calibration_id
    assert snapshot.baseline_enabled
    assert snapshot.baseline_lam == 4e5
    assert snapshot.baseline_order == 3
    assert snapshot.baseline_max_iter == 18
    window.close()
    app.processEvents()
