"""Input widgets that change values only through explicit text selection/input."""

from ..qt import QtCore, QtWidgets


_BLOCKED_STEP_KEYS = {
    QtCore.Qt.Key_Up,
    QtCore.Qt.Key_Down,
    QtCore.Qt.Key_PageUp,
    QtCore.Qt.Key_PageDown,
}


class _DirectInputMixin:
    def _configure_direct_input(self) -> None:
        try:
            no_buttons = QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons
        except AttributeError:  # PyQt5
            no_buttons = QtWidgets.QAbstractSpinBox.NoButtons
        self.setButtonSymbols(no_buttons)
        self.setKeyboardTracking(False)

    def wheelEvent(self, event) -> None:
        event.ignore()

    def keyPressEvent(self, event) -> None:
        if event.key() in _BLOCKED_STEP_KEYS:
            event.accept()
            return
        super().keyPressEvent(event)


class DirectSpinBox(_DirectInputMixin, QtWidgets.QSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._configure_direct_input()


class DirectDoubleSpinBox(_DirectInputMixin, QtWidgets.QDoubleSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._configure_direct_input()


class NoWheelComboBox(QtWidgets.QComboBox):
    def wheelEvent(self, event) -> None:
        event.ignore()
