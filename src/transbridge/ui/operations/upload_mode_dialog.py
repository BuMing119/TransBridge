# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Choose the file layout before opening the upload window."""

from PyQt6.QtWidgets import QButtonGroup, QDialog, QDialogButtonBox, QLabel, QRadioButton, QVBoxLayout

from transbridge.ui.foundation.components import configure_dialog


class UploadModeDialog(QDialog):
    def __init__(self, parent=None):
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

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("下一步")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def categorized(self) -> bool:
        return self._categorized.isChecked()
