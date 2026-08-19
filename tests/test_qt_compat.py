from spectrometer.qt import (
    Property,
    QT_API,
    QtCore,
    QtSerialPort,
    QtWidgets,
    Signal,
    Slot,
)


def test_supported_qt_binding_exposes_required_api():
    assert QT_API in {"PyQt6", "PySide6", "PyQt5"}
    assert Signal is not None
    assert Slot is not None
    assert Property is not None
    assert QtCore.QByteArray is not None
    assert QtSerialPort.QSerialPort is not None
    assert QtSerialPort.QSerialPortInfo is not None


def test_flat_enum_aliases_cover_supported_ui_paths():
    assert QtCore.QEvent.KeyPress == QtCore.QEvent.Type.KeyPress
    assert QtCore.Qt.NoModifier == QtCore.Qt.KeyboardModifier.NoModifier
    assert QtCore.Qt.PreciseTimer == QtCore.Qt.TimerType.PreciseTimer
    assert QtWidgets.QDialog.Accepted == QtWidgets.QDialog.DialogCode.Accepted
    assert QtWidgets.QDialog.Rejected == QtWidgets.QDialog.DialogCode.Rejected
