from pathlib import Path

from ..qt import QtWidgets
from .input_controls import (
    DirectDoubleSpinBox,
    DirectSpinBox,
    NoWheelComboBox,
)


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("系统选项"); self.setMinimumWidth(520)
        layout = QtWidgets.QFormLayout(self)
        path_row = QtWidgets.QWidget(); path_layout = QtWidgets.QHBoxLayout(path_row); path_layout.setContentsMargins(0, 0, 0, 0)
        self.storage_path = QtWidgets.QLineEdit(str(settings.get("storage_path", "data"))); path_layout.addWidget(self.storage_path, 1)
        browse = QtWidgets.QPushButton("浏览…"); browse.clicked.connect(self._browse); path_layout.addWidget(browse)
        layout.addRow("数据目录", path_row)
        self.line_width = DirectDoubleSpinBox(); self.line_width.setRange(0.5, 5); self.line_width.setSingleStep(0.1)
        self.line_width.setValue(float(settings.get("line_width", 1.4))); layout.addRow("曲线宽度", self.line_width)
        self.batch_size = DirectSpinBox()
        self.batch_size.setRange(1, 1000)
        self.batch_size.setValue(int(settings.get("batch_size", 500)))
        layout.addRow("每批帧数", self.batch_size)
        self.storage_format = NoWheelComboBox()
        self.storage_format.addItem("CSV + Excel", "csv_excel")
        self.storage_format.addItem("仅 CSV", "csv")
        self.storage_format.addItem("仅 Excel", "excel")
        index = self.storage_format.findData(
            settings.get("storage_format", "csv_excel")
        )
        self.storage_format.setCurrentIndex(max(0, index))
        layout.addRow("存储格式", self.storage_format)
        self.auto_store = QtWidgets.QCheckBox("采集时自动批量存储")
        self.auto_store.setChecked(bool(settings.get("auto_store", False)))
        layout.addRow(self.auto_store)
        note = QtWidgets.QLabel("Excel：一个设备一个工作表；CSV：一个设备一个文件。\n两种格式均为每帧一列、每像素一行。")
        note.setWordWrap(True); layout.addRow("存储布局", note)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addRow(buttons)

    def _browse(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择数据目录", self.storage_path.text())
        if path: self.storage_path.setText(path)

    def values(self):
        return {
            "storage_path": self.storage_path.text().strip() or "data",
            "line_width": self.line_width.value(),
            "batch_size": self.batch_size.value(),
            "storage_format": self.storage_format.currentData(),
            "auto_store": self.auto_store.isChecked(),
        }
