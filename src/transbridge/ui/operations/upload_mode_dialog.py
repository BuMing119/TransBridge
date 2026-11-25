# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Choose the file layout before opening the upload window."""

from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from transbridge.application.upload_policy import ProjectRole
from transbridge.ui.foundation.components import configure_dialog


def apply_operation_access(combo, access):
    first = -1
    for index in range(combo.count()):
        reason = access.operation_denial(combo.itemData(index))
        item = combo.model().item(index)
        item.setToolTip(reason or "")
        item.setEnabled(reason is None)
        if first < 0 and reason is None:
            first = index
    combo.setCurrentIndex(first)


class UploadModeDialog(QDialog):
    def __init__(self, parent=None, *, access=None, edit=None):
        super().__init__(parent)
        configure_dialog(self)
        self.setWindowTitle("上传到 ParaTranz")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("选择上传方式", self))

        self._plain = QRadioButton("普通上传", self)
        self._categorized = QRadioButton("分类上传", self)
        self._group = QButtonGroup(self)
        for button in (self._plain, self._categorized):
            self._group.addButton(button)
            layout.addWidget(button)
        self._plain.setChecked(True)
        self.access = access
        self._edit = edit
        if access is not None:
            self._apply_layout()
            if access.role == ProjectRole.OWNER and edit is not None:
                settings = QPushButton("编辑项目政策", self)
                settings.clicked.connect(self._edit_policy)
                layout.addWidget(settings)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("下一步")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _apply_layout(self):
        for categorized, button in ((False, self._plain), (True, self._categorized)):
            allowed = self.access.allows_layout(categorized)
            button.setEnabled(allowed)
            required = "普通上传" if categorized else "分类上传"
            button.setToolTip("" if allowed else f"项目规定使用{required}。")
        if not self._plain.isEnabled():
            self._categorized.setChecked(True)
        elif not self._categorized.isEnabled():
            self._plain.setChecked(True)

    def _edit_policy(self):
        try:
            access = self._edit(self.access, self)
        except (RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, "无法编辑政策", str(exc))
            self.reject()
            return
        if access is not None:
            self.access = access
            self._apply_layout()

    @property
    def categorized(self) -> bool:
        return self._categorized.isChecked()
