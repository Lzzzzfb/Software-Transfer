import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectrometer.qt import QtWidgets
from spectrometer.ui.y_axis_dialog import YAxisDialog, YAxisSettings


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = (
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    )
    return _APPLICATION


def test_y_axis_dialog_returns_valid_fixed_values():
    application()
    dialog = YAxisDialog(YAxisSettings(False, 0.0, 65535.0))
    dialog.fixed.setChecked(True)
    dialog.minimum.setValue(-12.5)
    dialog.maximum.setValue(234.5)

    dialog._accept_values()

    assert dialog.result() == QtWidgets.QDialog.Accepted
    assert dialog.values() == YAxisSettings(True, -12.5, 234.5)


def test_y_axis_dialog_rejects_reversed_fixed_range_without_closing():
    application()
    original = YAxisSettings(True, 0.0, 100.0)
    dialog = YAxisDialog(original)
    dialog.minimum.setValue(100.0)
    dialog.maximum.setValue(100.0)

    dialog._accept_values()

    assert dialog.result() != QtWidgets.QDialog.Accepted
    assert "最大值必须大于最小值" in dialog.error_label.text()
    assert dialog.values() == original


def test_y_axis_dialog_allows_disabling_fixed_range():
    application()
    dialog = YAxisDialog(YAxisSettings(True, -5.0, 5.0))
    dialog.fixed.setChecked(False)

    dialog._accept_values()

    assert dialog.result() == QtWidgets.QDialog.Accepted
    assert dialog.values() == YAxisSettings(False, -5.0, 5.0)
