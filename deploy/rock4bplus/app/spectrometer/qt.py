"""Qt 绑定兼容层：Linux 产品使用 PyQt6，开发环境兼容 PySide6/PyQt5。"""

import sys


def _load_qt():
    candidates = ("PyQt6", "PySide6", "PyQt5") if sys.platform.startswith("linux") else (
        "PySide6",
        "PyQt6",
        "PyQt5",
    )
    errors = []
    for candidate in candidates:
        try:
            if candidate == "PySide6":
                from PySide6 import QtCore, QtGui, QtWidgets, QtSerialPort

                return (
                    candidate,
                    QtCore,
                    QtGui,
                    QtWidgets,
                    QtSerialPort,
                    QtCore.Signal,
                    QtCore.Slot,
                    QtCore.Property,
                )
            if candidate == "PyQt6":
                from PyQt6 import QtCore, QtGui, QtWidgets, QtSerialPort

                return (
                    candidate,
                    QtCore,
                    QtGui,
                    QtWidgets,
                    QtSerialPort,
                    QtCore.pyqtSignal,
                    QtCore.pyqtSlot,
                    QtCore.pyqtProperty,
                )
            from PyQt5 import QtCore, QtGui, QtWidgets, QtSerialPort

            return (
                candidate,
                QtCore,
                QtGui,
                QtWidgets,
                QtSerialPort,
                QtCore.pyqtSignal,
                QtCore.pyqtSlot,
                QtCore.pyqtProperty,
            )
        except ImportError as exc:  # pragma: no cover - depends on host packages
            errors.append(f"{candidate}: {exc}")
    raise ImportError("未找到可用的 Qt 绑定；" + " | ".join(errors))


QT_API, QtCore, QtGui, QtWidgets, QtSerialPort, Signal, Slot, Property = _load_qt()


def _install_flat_enum_aliases():
    """让现有 Qt5/PySide 风格代码可在 PyQt6 scoped enums 下运行。"""

    if QT_API != "PyQt6":
        return
    mappings = (
        (
            QtCore.Qt,
            {
                "QueuedConnection": ("ConnectionType", "QueuedConnection"),
                "Horizontal": ("Orientation", "Horizontal"),
                "ToolButtonTextOnly": ("ToolButtonStyle", "ToolButtonTextOnly"),
                "PointingHandCursor": ("CursorShape", "PointingHandCursor"),
                "Key_Up": ("Key", "Key_Up"),
                "Key_Down": ("Key", "Key_Down"),
                "Key_PageUp": ("Key", "Key_PageUp"),
                "Key_PageDown": ("Key", "Key_PageDown"),
                "StrongFocus": ("FocusPolicy", "StrongFocus"),
                "AlignCenter": ("AlignmentFlag", "AlignCenter"),
                "AlignRight": ("AlignmentFlag", "AlignRight"),
                "AlignVCenter": ("AlignmentFlag", "AlignVCenter"),
                "DashLine": ("PenStyle", "DashLine"),
                "LeftButton": ("MouseButton", "LeftButton"),
            },
        ),
        (
            QtWidgets.QFrame,
            {
                "NoFrame": ("Shape", "NoFrame"),
                "VLine": ("Shape", "VLine"),
                "Sunken": ("Shadow", "Sunken"),
            },
        ),
        (
            QtWidgets.QDialogButtonBox,
            {
                "Apply": ("StandardButton", "Apply"),
                "Close": ("StandardButton", "Close"),
                "Ok": ("StandardButton", "Ok"),
                "Cancel": ("StandardButton", "Cancel"),
            },
        ),
    )
    for owner, aliases in mappings:
        for alias, (group, member) in aliases.items():
            if not hasattr(owner, alias):
                setattr(owner, alias, getattr(getattr(owner, group), member))


_install_flat_enum_aliases()


def dialog_exec(dialog):
    method = getattr(dialog, "exec", None) or dialog.exec_
    return method()


def application_exec(application):
    method = getattr(application, "exec", None) or application.exec_
    return method()
