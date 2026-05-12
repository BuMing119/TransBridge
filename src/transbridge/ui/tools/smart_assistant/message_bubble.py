from PyQt6.QtWidgets import QWidget, QLabel, QHBoxLayout, QVBoxLayout
from PyQt6.QtCore import Qt

from src.transbridge.infra.markdown_renderer import MarkdownRenderer

_RENDERER = MarkdownRenderer()


class MessageBubble(QWidget):
    """单条消息气泡，根据 role 渲染不同样式。内容使用 MarkdownRenderer 渲染。"""

    _STYLES = {
        "user": (
            "QWidget#BubbleInner {"
            "  background-color: #95EC69;"
            "  border-radius: 14px;"
            "  padding: 0px;"
            "}"
        ),
        "assistant": (
            "QWidget#BubbleInner {"
            "  background-color: #FFFFFF;"
            "  border: 1px solid #E0E0E0;"
            "  border-radius: 14px;"
            "  padding: 0px;"
            "}"
        ),
        "system": "",
    }

    def __init__(self, text: str, role: str, parent=None):
        super().__init__(parent)
        self.setObjectName(f"Bubble_{role}")

        outer = QHBoxLayout(self)
        outer.setContentsMargins(4, 2, 4, 2)

        if role == "system":
            label = QLabel(text)
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet(
                "color: #999999; font-size: 11px; padding: 4px 8px;"
            )
            outer.addWidget(label)
            return

        # User / Assistant: wrap rendered markdown in a styled container
        inner = QWidget()
        inner.setObjectName("BubbleInner")
        inner.setStyleSheet(self._STYLES.get(role, self._STYLES["assistant"]))
        inner.setMaximumWidth(420)

        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(12, 8, 12, 8)
        inner_layout.setSpacing(0)

        content = _RENDERER.render(text)
        inner_layout.addWidget(content)

        if role == "user":
            outer.addStretch()
            outer.addWidget(inner)
        else:
            outer.addWidget(inner)
            outer.addStretch()
