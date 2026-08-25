"""Document-flow message widgets with snapshot-driven presentation."""

from __future__ import annotations

import threading

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from transbridge.infra.markdown_renderer import MarkdownRenderer
from transbridge.ui.foundation.tabler_icons import tabler_icon

from .theme_support import SmartAssistantTheme

_RENDERER: MarkdownRenderer | None = None
_renderer_lock = threading.Lock()


def _get_renderer() -> MarkdownRenderer:
    global _RENDERER
    if _RENDERER is None:
        with _renderer_lock:
            if _RENDERER is None:
                _RENDERER = MarkdownRenderer()
    return _RENDERER


class AvatarLabel(QLabel):
    def __init__(self, role: str, parent=None, *, theme: SmartAssistantTheme | None = None):
        super().__init__(parent)
        self._role = role
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(30, 30)
        self.setAccessibleName("用户头像" if role == "user" else "助手头像")
        self.setStyleSheet("QLabel { border-radius: 15px; }")
        self.apply_theme(theme or SmartAssistantTheme())

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        theme.apply_surface(self, alternate=self._role == "user")
        icon_name = "sparkles" if self._role == "assistant" else "user"
        semantic = "accent" if self._role == "assistant" else "navigation"
        self.setPixmap(tabler_icon(self, icon_name, 18, semantic=semantic).pixmap(18, 18))


class MessageBubble(QWidget):
    """A message whose text is invariant across theme revisions."""

    _PLAINTEXT_FALLBACK_LENGTH = 10000

    def __init__(
        self,
        text: str,
        role: str,
        parent=None,
        *,
        theme: SmartAssistantTheme | None = None,
    ):
        super().__init__(parent)
        self._text = text
        self._role = role
        self._theme = theme or SmartAssistantTheme()
        self.setObjectName(f"Message_{role}")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.setAccessibleName("用户消息" if role == "user" else "助手消息")
        self.setAccessibleDescription(text)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 12, 0, 12)
        outer.setSpacing(12)
        self._avatar = AvatarLabel(role, theme=self._theme)

        self._content_column = QWidget()
        column_width_policy = QSizePolicy.Policy.Preferred if role == "user" else QSizePolicy.Policy.Expanding
        self._content_column.setSizePolicy(column_width_policy, QSizePolicy.Policy.Maximum)
        column_layout = QVBoxLayout(self._content_column)
        column_layout.setContentsMargins(0, 0, 0, 0)
        column_layout.setSpacing(6)
        self._role_label = QLabel("TransBridge 智能助手" if role == "assistant" else "你")
        self._role_label.setFont(QFont("Microsoft YaHei", 9, QFont.Weight.DemiBold))
        self._role_label.setVisible(role == "assistant")
        column_layout.addWidget(self._role_label)

        self._content_wrapper = QWidget()
        wrapper_width_policy = QSizePolicy.Policy.Preferred if role == "user" else QSizePolicy.Policy.Expanding
        self._content_wrapper.setSizePolicy(wrapper_width_policy, QSizePolicy.Policy.Maximum)
        self._content_wrapper.setProperty("tbSurface", "message")
        wrapper_layout = QVBoxLayout(self._content_wrapper)
        wrapper_layout.setContentsMargins(
            14 if role == "user" else 0,
            10 if role == "user" else 0,
            14 if role == "user" else 0,
            10 if role == "user" else 0,
        )
        wrapper_layout.setSpacing(0)
        self._content = self._render_content(text)
        self._content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        wrapper_layout.addWidget(self._content)
        self._content_wrapper.setMaximumWidth(540 if role == "user" else 760)
        column_layout.addWidget(self._content_wrapper)

        if role == "user":
            outer.addStretch(1)
            outer.addWidget(self._content_column)
            outer.addWidget(self._avatar, alignment=Qt.AlignmentFlag.AlignTop)
        else:
            outer.addWidget(self._avatar, alignment=Qt.AlignmentFlag.AlignTop)
            outer.addWidget(self._content_column, stretch=1)
        self.apply_theme(self._theme)

    @property
    def text(self) -> str:
        return self._text

    @property
    def role(self) -> str:
        return self._role

    def _render_content(self, text: str):
        if len(text) > self._PLAINTEXT_FALLBACK_LENGTH:
            label = QLabel(text)
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
            return label
        return _get_renderer().render(text, theme=self._theme.markdown_theme(alternate=self._role == "user"))

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme
        self._avatar.apply_theme(theme)
        theme.apply_semantic(self._role_label, "default")
        theme.apply_surface(self._content_column)
        theme.apply_surface(self._content_wrapper, alternate=self._role == "user")
        self._replace_content(self._text)

    def set_text(self, text: str) -> None:
        self._text = text
        self.setAccessibleDescription(text)
        self._replace_content(text)

    def _replace_content(self, text: str) -> None:
        wrapper_layout = self._content_wrapper.layout()
        if wrapper_layout is None:
            return
        old_content = getattr(self, "_content", None)
        if old_content is not None:
            wrapper_layout.removeWidget(old_content)
            old_content.deleteLater()
        self._content = self._render_content(text)
        self._content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._theme.apply_surface(self._content, alternate=self._role == "user")
        wrapper_layout.addWidget(self._content)


__all__ = ["AvatarLabel", "MessageBubble"]
