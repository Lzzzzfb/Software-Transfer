from datetime import datetime

from ..qt import QtWidgets


class DiagnosticsPanel(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        self.summary = QtWidgets.QTableWidget(0, 6)
        self.summary.setHorizontalHeaderLabels(["设备", "接收帧", "缺帧", "重复", "乱序", "回绕"])
        self.summary.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.summary)
        self.log = QtWidgets.QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(3000)
        layout.addWidget(self.log, 1)

    def append(self, message: str, level: str = "INFO"):
        self.log.appendPlainText(f"{datetime.now():%H:%M:%S.%f}"[:-3] + f" [{level}] {message}")

    def update_device(self, device_id, diagnostics):
        row = None
        for index in range(self.summary.rowCount()):
            if self.summary.item(index, 0).data(32) == device_id:
                row = index; break
        if row is None:
            row = self.summary.rowCount(); self.summary.insertRow(row)
            item = QtWidgets.QTableWidgetItem(str(device_id)); item.setData(32, device_id); self.summary.setItem(row, 0, item)
        values = [diagnostics.received, diagnostics.missing, diagnostics.duplicates, diagnostics.out_of_order, diagnostics.wraps]
        for column, value in enumerate(values, 1): self.summary.setItem(row, column, QtWidgets.QTableWidgetItem(str(value)))
