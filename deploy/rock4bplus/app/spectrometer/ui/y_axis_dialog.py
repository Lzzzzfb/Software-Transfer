"""固定 Y 轴范围设置。"""

from dataclasses import dataclass

from ..qt import QtWidgets
from .input_controls import DirectDoubleSpinBox


@dataclass(frozen=True)
class YAxisSettings:
    fixed: bool = False
    minimum: float = 0.0
    maximum: float = 65535.0


class YAxisDialog(QtWidgets.QDialog):
    def __init__(self, settings: YAxisSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Y 轴设置")
        self.setMinimumWidth(360)
        self._original = settings
        self._values = settings

        layout = QtWidgets.QFormLayout(self)
        self.fixed = QtWidgets.QCheckBox("固定 Y 轴范围")
        self.fixed.setChecked(settings.fixed)
        layout.addRow(self.fixed)

        self.minimum = DirectDoubleSpinBox()
        self.minimum.setDecimals(6)
        self.minimum.setRange(-1e12, 1e12)
        self.minimum.setValue(settings.minimum)
        layout.addRow("最小值", self.minimum)

        self.maximum = DirectDoubleSpinBox()
        self.maximum.setDecimals(6)
        self.maximum.setRange(-1e12, 1e12)
        self.maximum.setValue(settings.maximum)
        layout.addRow("最大值", self.maximum)

        self.error_label = QtWidgets.QLabel()
        self.error_label.setStyleSheet("color: #c62828;")
        self.error_label.setWordWrap(True)
        layout.addRow(self.error_label)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok
            | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._accept_values)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _accept_values(self):
        minimum = self.minimum.value()
        maximum = self.maximum.value()
        if self.fixed.isChecked() and maximum <= minimum:
            self.error_label.setText("最大值必须大于最小值")
            return
        self.error_label.clear()
        self._values = YAxisSettings(
            self.fixed.isChecked(),
            minimum,
            maximum,
        )
        self.accept()

    def values(self) -> YAxisSettings:
        return self._values
