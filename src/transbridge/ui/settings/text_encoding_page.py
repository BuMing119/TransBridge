# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Program defaults for newly created projects and standalone file operations."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from transbridge.ui.text_encoding_fields import TextEncodingFields


class TextEncodingSettingsPage(QWidget):
    changed = pyqtSignal()

    def __init__(self, repository, parent=None) -> None:
        super().__init__(parent)
        self._repository = repository
        self._loaded = None
        layout = QVBoxLayout(self)
        scope = QLabel(
            "设置 Skyrim SE 插件及 Strings 的默认语言和编码。新项目创建时复制这组值，独立文件操作也使用这组值。"
            "已有项目可在“项目 → 文本编码”中修改。",
            self,
        )
        scope.setWordWrap(True)
        layout.addWidget(scope)
        try:
            self._loaded = repository.load()
        except (OSError, RuntimeError, ValueError) as exc:
            error = QLabel(f"无法读取默认文本编码：{exc}。请检查配置后重新打开设置。", self)
            error.setTextFormat(Qt.TextFormat.PlainText)
            error.setWordWrap(True)
            layout.addWidget(error)
        else:
            self.fields = TextEncodingFields(self._loaded, self, prefix="默认")
            layout.addWidget(self.fields)
            self.fields.changed.connect(self.changed)
        layout.addStretch()

    @property
    def dirty(self) -> bool:
        return self._loaded is not None and self.fields.value() != self._loaded

    def save(self) -> None:
        if self.dirty:
            encodings = self.fields.value()
            self._repository.save(encodings)
            self._loaded = encodings
