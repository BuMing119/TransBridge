"""Document-flow message widgets with snapshot-driven presentation."""

from __future__ import annotations

import threading

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from transbridge.infra.markdown_renderer import MarkdownRenderer

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
    _ROLE_LETTERS = {"user": "U", "assistant": "A"}

    def __init__(self, role: str, parent=None, *, theme: SmartAssistantTheme | None = None):
        super().__init__(parent)
        self._role = role
        self.setText(self._ROLE_LETTERS.get(role, "?"))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(24, 24)
        self.setAccessibleName("用户头像" if role == "user" else "助手头像")
        self.setStyleSheet("QLabel { border-radius: 12px; font-size: 11px; font-weight: bold; }")
        self.apply_theme(theme or SmartAssistantTheme())

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        theme.apply_semantic(self, "primary" if self._role == "assistant" else "muted", background=True)


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
        self.setAccessibleName("用户消息" if role == "user" else "助手消息")
        self.setAccessibleDescription(text)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 8)
        outer.setSpacing(10)
        self._avatar = AvatarLabel(role, theme=self._theme)
        outer.addWidget(self._avatar, alignment=Qt.AlignmentFlag.AlignTop)
        self._content_wrapper = QWidget()
        self._content_wrapper.setProperty("tbSurface", "message")
        wrapper_layout = QVBoxLayout(self._content_wrapper)
        wrapper_layout.setContentsMargins(8 if role == "user" else 0, 2, 8 if role == "user" else 0, 2)
        wrapper_layout.setSpacing(0)
        self._content = self._render_content(text)
        wrapper_layout.addWidget(self._content)
        outer.addWidget(self._content_wrapper, stretch=1)
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
        return _get_renderer().render(text, theme=self._theme.markdown_theme())

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme
        self._avatar.apply_theme(theme)
        theme.apply_semantic(self._content_wrapper, "muted" if self._role == "user" else "default", background=True)
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
        theme_state = "muted" if self._role == "user" else "default"
        self._theme.apply_semantic(self._content, theme_state)
        wrapper_layout.addWidget(self._content)


__all__ = ["AvatarLabel", "MessageBubble"]
