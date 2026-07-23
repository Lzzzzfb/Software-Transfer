import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectrometer.qt import QtCore, QtGui, QtWidgets
from spectrometer.ui.input_controls import (
    DirectDoubleSpinBox,
    DirectSpinBox,
    NoWheelComboBox,
)


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return _APPLICATION


class IgnorableWheelEvent:
    def __init__(self):
        self.ignored = False

    def ignore(self):
        self.ignored = True


def no_buttons_value():
    try:
        return QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons
    except AttributeError:
        return QtWidgets.QAbstractSpinBox.NoButtons


def test_numeric_inputs_hide_buttons_and_reject_step_keys():
    application()
    for widget in (DirectSpinBox(), DirectDoubleSpinBox()):
        widget.setRange(0, 100)
        widget.setValue(50)
        assert widget.buttonSymbols() == no_buttons_value()
        for key in (
            QtCore.Qt.Key_Up,
            QtCore.Qt.Key_Down,
            QtCore.Qt.Key_PageUp,
            QtCore.Qt.Key_PageDown,
        ):
            event = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key, QtCore.Qt.NoModifier)
            widget.keyPressEvent(event)
            assert widget.value() == 50


def test_numeric_and_combo_inputs_ignore_wheel_events():
    application()
    widgets = [DirectSpinBox(), DirectDoubleSpinBox(), NoWheelComboBox()]
    for widget in widgets:
        event = IgnorableWheelEvent()
        widget.wheelEvent(event)
        assert event.ignored
