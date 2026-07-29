from datetime import datetime

from ..qt import QtWidgets


class DiagnosticsPanel(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        self.summary = QtWidgets.QTableWidget(0, 10)
        self.summary.setHorizontalHeaderLabels(
            [
                "设备",
                "完整帧",
                "已保存",
                "显示帧",
                "显示覆盖",
                "缺帧",
                "拒绝候选",
                "重同步",
                "协议",
                "状态",
            ]
        )
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

    def update_acquisition(self, device_id, values):
        row = None
        for index in range(self.summary.rowCount()):
            if self.summary.item(index, 0).data(32) == device_id:
                row = index
                break
        if row is None:
            row = self.summary.rowCount()
            self.summary.insertRow(row)
            item = QtWidgets.QTableWidgetItem(str(device_id))
            item.setData(32, device_id)
            self.summary.setItem(row, 0, item)
        columns = [
            values.get("raw_complete_frames", 0),
            values.get("persisted_frames", 0),
            values.get("display_frames", 0),
            values.get("display_overwrites", 0),
            values.get("missing_frames", 0),
            values.get("invalid_headers", 0),
            values.get("resync_count", 0),
            values.get("protocol_variant", ""),
            "采集中",
        ]
        for column, value in enumerate(columns, 1):
            self.summary.setItem(
                row, column, QtWidgets.QTableWidgetItem(str(value))
            )
