from pathlib import Path

from ..qt import QtWidgets


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("系统选项"); self.setMinimumWidth(520)
        layout = QtWidgets.QFormLayout(self)
        path_row = QtWidgets.QWidget(); path_layout = QtWidgets.QHBoxLayout(path_row); path_layout.setContentsMargins(0, 0, 0, 0)
        self.storage_path = QtWidgets.QLineEdit(str(settings.get("storage_path", "data"))); path_layout.addWidget(self.storage_path, 1)
        browse = QtWidgets.QPushButton("浏览…"); browse.clicked.connect(self._browse); path_layout.addWidget(browse)
        layout.addRow("数据目录", path_row)
        self.line_width = QtWidgets.QDoubleSpinBox(); self.line_width.setRange(0.5, 5); self.line_width.setSingleStep(0.1)
        self.line_width.setValue(float(settings.get("line_width", 1.4))); layout.addRow("曲线宽度", self.line_width)
        note = QtWidgets.QLabel("Excel：一个设备一个工作表；CSV：一个设备一个文件。\n两种格式均为每帧一列、每像素一行。")
        note.setWordWrap(True); layout.addRow("存储布局", note)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addRow(buttons)

    def _browse(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择数据目录", self.storage_path.text())
        if path: self.storage_path.setText(path)

    def values(self):
        return {"storage_path": self.storage_path.text().strip() or "data", "line_width": self.line_width.value()}
