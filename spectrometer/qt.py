"""Qt 绑定兼容层：产品优先 PySide6，本机开发可回退 PyQt5。"""

try:
    from PySide6 import QtCore, QtGui, QtWidgets, QtSerialPort

    Signal = QtCore.Signal
    Slot = QtCore.Slot
    Property = QtCore.Property
    QT_API = "PySide6"
except ImportError:  # pragma: no cover - 由当前开发机覆盖，发布环境走上方
    from PyQt5 import QtCore, QtGui, QtWidgets, QtSerialPort

    Signal = QtCore.pyqtSignal
    Slot = QtCore.pyqtSlot
    Property = QtCore.pyqtProperty
    QT_API = "PyQt5"


def dialog_exec(dialog):
    method = getattr(dialog, "exec", None) or dialog.exec_
    return method()


def application_exec(application):
    method = getattr(application, "exec", None) or application.exec_
    return method()
