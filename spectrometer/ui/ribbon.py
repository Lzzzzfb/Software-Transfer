"""参考原软件双层工具带的现代化 Ribbon。"""

from ..qt import QtCore, QtWidgets, Signal


class RibbonButton(QtWidgets.QToolButton):
    def __init__(self, symbol: str, text: str, parent=None):
        super().__init__(parent)
        self.setText(f"{symbol}\n{text}")
        self.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        self.setMinimumSize(72, 58)
        self.setCursor(QtCore.Qt.PointingHandCursor)


class MainRibbon(QtWidgets.QWidget):
    start_requested = Signal()
    stop_requested = Signal()
    background_requested = Signal()
    reference_requested = Signal()
    sync_mode_changed = Signal(str)
    discover_requested = Signal()
    new_history_requested = Signal()
    settings_requested = Signal()
    help_requested = Signal()
    exit_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("mainRibbon")
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(4)
        self.buttons = {}
        definitions = [
            ("start", "▶", "开始", self.start_requested),
            ("stop", "■", "停止", self.stop_requested),
            ("background", "▣", "背景", self.background_requested),
            ("reference", "◉", "参考", self.reference_requested),
        ]
        for key, symbol, label, signal in definitions:
            button = RibbonButton(symbol, label)
            button.clicked.connect(signal)
            layout.addWidget(button); self.buttons[key] = button
        layout.addWidget(self._separator())

        sync_group = QtWidgets.QWidget(); sync_layout = QtWidgets.QVBoxLayout(sync_group)
        sync_layout.setContentsMargins(6, 0, 6, 0); sync_layout.setSpacing(2)
        sync_layout.addWidget(QtWidgets.QLabel("同步模式"))
        self.sync_combo = QtWidgets.QComboBox()
        self.sync_combo.addItem("独立采集", "independent")
        self.sync_combo.addItem("软件同步", "software")
        self.sync_combo.addItem("内部硬同步", "hard_internal")
        self.sync_combo.addItem("外部硬同步", "hard_external")
        self.sync_combo.currentIndexChanged.connect(
            lambda: self.sync_mode_changed.emit(self.sync_combo.currentData())
        )
        sync_layout.addWidget(self.sync_combo); layout.addWidget(sync_group)
        layout.addWidget(self._separator())

        for key, symbol, label, signal in [
            ("discover", "⟳", "查找设备", self.discover_requested),
            ("history", "＋", "新建页面", self.new_history_requested),
            ("settings", "⚙", "选项", self.settings_requested),
            ("help", "?", "帮助", self.help_requested),
            ("exit", "⏻", "退出", self.exit_requested),
        ]:
            button = RibbonButton(symbol, label)
            button.clicked.connect(signal)
            layout.addWidget(button); self.buttons[key] = button
        layout.addStretch(1)

    @staticmethod
    def _separator():
        line = QtWidgets.QFrame(); line.setFrameShape(QtWidgets.QFrame.VLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken); return line
