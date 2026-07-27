from spectrometer.qt import (
    Property,
    QT_API,
    QtCore,
    QtSerialPort,
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
