import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectrometer.processing.profiles import AirplsProfile
from spectrometer.qt import QtWidgets
from spectrometer.ui.airpls_dialog import AirplsDialog


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return _APPLICATION


def test_airpls_dialog_round_trips_profile_and_restores_defaults():
    application()
    dialog = AirplsDialog(
        AirplsProfile(enabled=True, lam=3e5, order=3, max_iter=24)
    )
    assert dialog.profile() == AirplsProfile(
        enabled=True,
        lam=3e5,
        order=3,
        max_iter=24,
    )

    dialog.restore_defaults()

    assert dialog.profile() == AirplsProfile()
