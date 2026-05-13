from PyQt6.QtWidgets import QWidget, QLabel, QHBoxLayout, QVBoxLayout
from PyQt6.QtCore import Qt

from src.transbridge.infra.markdown_renderer import MarkdownRenderer

# m26: 延迟初始化 MarkdownRenderer，避免模块导入时依赖 QApplication
_RENDERER: MarkdownRenderer | None = None


def _get_renderer() -> MarkdownRenderer:
    global _RENDERER
    if _RENDERER is None:
        _RENDERER = MarkdownRenderer()
    return _RENDERER


class MessageBubble(QWidget):
    """单条消息气泡，根据 role 渲染不同样式。内容使用 MarkdownRenderer 渲染。"""

    _BUBBLE_MAX_WIDTH = 420

    _STYLES = {
        "user": (
            "QWidget#BubbleInner {"
            "  background-color: #95EC69;"
            "  border-radius: 14px;"
            "}"
        ),
        "assistant": (
            "QWidget#BubbleInner {"
            "  background-color: #FFFFFF;"
            "  border: 1px solid #E0E0E0;"
            "  border-radius: 14px;"
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
            self._role = role
            self._system_label = label
            self._inner = None
            self._content = None
            return

        # User / Assistant: wrap rendered markdown in a styled container
        inner = QWidget()
        inner.setObjectName("BubbleInner")
        inner.setStyleSheet(self._STYLES.get(role, self._STYLES["assistant"]))
        inner.setMaximumWidth(self._BUBBLE_MAX_WIDTH)

        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(12, 8, 12, 8)
        inner_layout.setSpacing(0)

        content = _get_renderer().render(text)
        inner_layout.addWidget(content)

        if role == "user":
            outer.addStretch()
            outer.addWidget(inner)
        else:
            outer.addWidget(inner)
            outer.addStretch()

        self._role = role
        self._inner = inner
        self._content = content
        self._system_label = None

    def set_text(self, text: str) -> None:
        """就地更新文本内容，复用气泡外壳避免 QWidget 重建。（CR2 修复）"""
        if self._role == "system":
            # system 角色直接更新 QLabel
            if self._system_label:
                self._system_label.setText(text)
            return
        if self._inner is None:
            return
        inner_layout = self._inner.layout()
        if inner_layout is None:
            return
        # 移除旧内容
        if self._content:
            inner_layout.removeWidget(self._content)
            self._content.deleteLater()
        # 渲染新内容
        self._content = _get_renderer().render(text)
        inner_layout.addWidget(self._content)
