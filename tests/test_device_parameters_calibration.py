import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectrometer.device.device_manager import DeviceManager
from spectrometer.processing.calibration_repository import (
    IntensityCalibrationRepository,
)
from spectrometer.processing.profile_repository import (
    ProcessingProfileRepository,
)
from spectrometer.processing.profiles import AirplsProfile
from spectrometer.qt import QtWidgets
from spectrometer.ui.device_parameters import DeviceParametersDialog


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return _APPLICATION


def test_dialog_imports_and_clears_intensity_calibration(
    tmp_path, monkeypatch
):
    application()
    manager = DeviceManager()
    device_id = manager.add_simulated_device(
        "SIM1", "SN003", 4, 400.0, 1.0
    )
    device = manager.get_device(device_id)
    source = tmp_path / "calibration.csv"
    source.write_text(
        "Pixel,Coefficient\n0,1\n1,1.1\n2,1.2\n3,1.3\n",
        encoding="utf-8",
    )
    calibration_repository = IntensityCalibrationRepository(
        tmp_path / "calibrations"
    )
    profile_repository = ProcessingProfileRepository(
        tmp_path / "profiles.json"
    )
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (str(source), ""),
    )
    dialog = DeviceParametersDialog(
        device,
        manager,
        global_airpls_profile=AirplsProfile(),
        calibration_repository=calibration_repository,
        profile_repository=profile_repository,
        can_modify=lambda: True,
    )

    dialog._import_intensity_calibration()

    assert device.intensity_calibration_record is not None
    assert tuple(device.intensity_calib) == (1.0, 1.1, 1.2, 1.3)
    assert calibration_repository.load("SN003", 4) is not None

    dialog._clear_intensity_calibration()

    assert device.intensity_calibration_record is None
    assert device.intensity_calib is None
    assert calibration_repository.load("SN003", 4) is None


def test_dialog_persists_device_airpls_override(tmp_path):
    application()
    manager = DeviceManager()
    device_id = manager.add_simulated_device(
        "SIM1", "SN003", 8, 400.0, 1.0
    )
    device = manager.get_device(device_id)
    repository = ProcessingProfileRepository(tmp_path / "profiles.json")
    dialog = DeviceParametersDialog(
        device,
        manager,
        global_airpls_profile=AirplsProfile(),
        profile_repository=repository,
        can_modify=lambda: True,
    )
    dialog.airpls_follow_global.setChecked(False)
    dialog.airpls_enabled.setChecked(True)
    dialog.airpls_lam.setValue(500000.0)
    dialog.airpls_order.setValue(3)
    dialog.airpls_max_iter.setValue(20)

    dialog.apply()

    stored = repository.load("SN003")
    assert stored == device.airpls_override
    assert not stored.follow_global
    assert stored.enabled
    assert stored.lam == 500000.0
    assert stored.order == 3
    assert stored.max_iter == 20
