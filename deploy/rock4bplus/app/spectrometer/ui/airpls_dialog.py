"""全局 airPLS 参数对话框。"""

from ..processing.profiles import (
    AIRPLS_ITER_MAX,
    AIRPLS_ITER_MIN,
    AIRPLS_LAM_MAX,
    AIRPLS_LAM_MIN,
    AIRPLS_ORDER_MAX,
    AIRPLS_ORDER_MIN,
    AirplsProfile,
    ProfileValidationError,
)
from ..qt import QtWidgets
from .input_controls import DirectDoubleSpinBox, DirectSpinBox


class AirplsDialog(QtWidgets.QDialog):
    def __init__(self, profile: AirplsProfile, parent=None):
        super().__init__(parent)
        self.setWindowTitle("airPLS 基线校正参数")
        self.setMinimumWidth(420)

        layout = QtWidgets.QVBoxLayout(self)
        description = QtWidgets.QLabel(
            "airPLS 在强度校准和所选光谱处理之后执行。"
            "参数变更只影响下一次采集任务。"
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        form = QtWidgets.QFormLayout()
        self.enabled = QtWidgets.QCheckBox("启用 airPLS 基线校正")
        self.enabled.setChecked(profile.enabled)
        form.addRow("状态", self.enabled)

        self.lam = DirectDoubleSpinBox()
        self.lam.setDecimals(6)
        self.lam.setRange(AIRPLS_LAM_MIN, AIRPLS_LAM_MAX)
        self.lam.setValue(profile.lam)
        form.addRow("平滑参数 λ", self.lam)

        self.order = DirectSpinBox()
        self.order.setRange(AIRPLS_ORDER_MIN, AIRPLS_ORDER_MAX)
        self.order.setValue(profile.order)
        form.addRow("差分阶数", self.order)

        self.max_iter = DirectSpinBox()
        self.max_iter.setRange(AIRPLS_ITER_MIN, AIRPLS_ITER_MAX)
        self.max_iter.setValue(profile.max_iter)
        form.addRow("最大迭代次数", self.max_iter)
        layout.addLayout(form)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        defaults = QtWidgets.QPushButton("恢复默认")
        reset_role = getattr(
            getattr(QtWidgets.QDialogButtonBox, "ButtonRole", object),
            "ResetRole",
            getattr(QtWidgets.QDialogButtonBox, "ResetRole", None),
        )
        buttons.addButton(defaults, reset_role)
        defaults.clicked.connect(self.restore_defaults)
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def restore_defaults(self):
        profile = AirplsProfile()
        self.enabled.setChecked(profile.enabled)
        self.lam.setValue(profile.lam)
        self.order.setValue(profile.order)
        self.max_iter.setValue(profile.max_iter)

    def profile(self) -> AirplsProfile:
        return AirplsProfile(
            enabled=self.enabled.isChecked(),
            lam=self.lam.value(),
            order=self.order.value(),
            max_iter=self.max_iter.value(),
        )

    def _accept_if_valid(self):
        try:
            self.profile()
        except ProfileValidationError as exc:
            QtWidgets.QMessageBox.warning(self, "airPLS 参数无效", str(exc))
            return
        self.accept()
