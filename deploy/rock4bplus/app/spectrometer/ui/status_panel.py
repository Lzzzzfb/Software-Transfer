from ..qt import QtWidgets


class StatusPanel(QtWidgets.QStatusBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.device_label = QtWidgets.QLabel("设备 0")
        self.rate_label = QtWidgets.QLabel("显示 0.0 fps")
        self.sequence_label = QtWidgets.QLabel("缺帧 0")
        self.queue_label = QtWidgets.QLabel("存储队列 0%")
        self.disk_label = QtWidgets.QLabel("磁盘 --")
        self.state_label = QtWidgets.QLabel("就绪")
        self.addWidget(self.state_label, 1)
        for widget in (self.device_label, self.rate_label, self.sequence_label, self.queue_label, self.disk_label):
            self.addPermanentWidget(widget)

    def update_metrics(self, devices, fps, missing, queue_ratio, disk_free_gb):
        self.device_label.setText(f"设备 {devices}")
        self.rate_label.setText(f"显示 {fps:.1f} fps")
        self.sequence_label.setText(f"缺帧 {missing}")
        self.queue_label.setText(f"存储队列 {queue_ratio * 100:.0f}%")
        self.disk_label.setText(f"磁盘 {disk_free_gb:.1f} GB")
        self.queue_label.setProperty("warning", queue_ratio >= 0.5)
        self.queue_label.style().unpolish(self.queue_label); self.queue_label.style().polish(self.queue_label)
