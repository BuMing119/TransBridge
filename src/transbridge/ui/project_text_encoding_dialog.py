# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Edit project language and codec settings without re-reading saved sources."""

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from transbridge.ui.foundation.components import configure_dialog
from transbridge.ui.text_encoding_fields import TextEncodingFields


class ProjectTextEncodingDialog(QDialog):
    def __init__(self, encodings, parent=None) -> None:
        super().__init__(parent)
        configure_dialog(self)
        self.setWindowTitle("项目文本编码")
        self.setAccessibleName("项目文本编码")
        self.resize(520, 350)
        layout = QVBoxLayout(self)
        scope = QLabel("适用于项目内 Skyrim SE 插件及 Strings。SST、EET、XML 保留各自格式的编码。", self)
        scope.setWordWrap(True)
        layout.addWidget(scope)
        self.fields = TextEncodingFields(encodings, self)
        layout.addWidget(self.fields)
        notice = QLabel("保存设置不会改动已有原文。需要应用新的读取规则时，请使用“更新来源”。", self)
        notice.setWordWrap(True)
        layout.addWidget(notice)
        layout.addStretch()
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self
        )
        self._next = self._buttons.button(QDialogButtonBox.StandardButton.Save)
        self._next.setText("保存")
        self._buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)
        self.fields.changed.connect(lambda: self._next.setEnabled(self.fields.value() != encodings))
        self._next.setEnabled(False)
