"""Document-flow message widgets with snapshot-driven presentation."""

from __future__ import annotations

import threading

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from transbridge.infra.markdown_renderer import MarkdownRenderer
from transbridge.ui.foundation.tabler_icons import tabler_icon

from .submission_scope import SubmissionScopeView
from .theme_support import QUIET_BUTTON_STYLE, SmartAssistantTheme

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


class _MessageCopyButton(QPushButton):
    """Keep the keyboard target and geometry, painting only while discoverable."""

    def __init__(self):
        super().__init__("已复制")
        self.setStyleSheet(QUIET_BUTTON_STYLE)
        self.setFixedWidth(self.sizeHint().width())
        self.setText("复制")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.revealed = False

    def set_revealed(self, revealed):
        if self.revealed != revealed:
            self.revealed = revealed
            self.update()

    def paintEvent(self, event):
        if self.revealed or self.hasFocus():
            super().paintEvent(event)


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
        self._role_label = QLabel("助手" if role == "assistant" else "你")
        self._role_label.hide()  # Avatar and accessible message name already identify the speaker.
        actions = QHBoxLayout()
        actions.addWidget(self._role_label)
        self._location_label = QLabel("已定位此消息")
        self._location_label.hide()
        actions.addWidget(self._location_label)
        actions.addStretch()
        self._scope_view = SubmissionScopeView(self._theme, compact=True)
        actions.addWidget(self._scope_view)
        self.copy_button = _MessageCopyButton()
        self.copy_button.setAccessibleName("复制完整回复" if role == "assistant" else "复制完整消息")
        self.copy_button.setToolTip("复制整段原始文本（包含 Markdown）")
        self.copy_button.clicked.connect(self.copy_text)
        actions.addWidget(self.copy_button)

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
        self._content = None  # apply_theme below renders once with the final palette.
        self._content_wrapper.setMaximumWidth(540 if role == "user" else 760)
        column_layout.addWidget(self._content_wrapper)
        column_layout.addLayout(actions)

        if role == "user":
            outer.addStretch(1)
            outer.addWidget(self._content_column)
            outer.addWidget(self._avatar, alignment=Qt.AlignmentFlag.AlignTop)
        else:
            outer.addWidget(self._avatar, alignment=Qt.AlignmentFlag.AlignTop)
            outer.addWidget(self._content_column, stretch=1)
        self.apply_theme(self._theme)
        QApplication.instance().focusChanged.connect(self._focus_changed)

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
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
            )
            return label
        return _get_renderer().render(text, theme=self._theme.markdown_theme(alternate=self._role == "user"))

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme
        self._avatar.apply_theme(theme)
        theme.apply_semantic(self._role_label, "default")
        theme.apply_semantic(self._location_label, "primary")
        theme.apply_surface(self.copy_button)
        theme.apply_surface(self._content_column)
        theme.apply_surface(self._content_wrapper, alternate=self._role == "user")
        self._scope_view.apply_theme(theme)
        self._replace_content(self._text)

    def set_scope(self, scope) -> None:
        self._scope_view.set_scope(scope)

    def set_text(self, text: str) -> None:
        self.update_stream_text(text)
        self._replace_content(text)

    def update_stream_text(self, text: str) -> None:
        """Keep full copy/accessibility text current without rendering each stream token twice."""
        self._text = text
        self.setAccessibleDescription(text)
        self.copy_button.setText("复制")

    def copy_text(self) -> None:
        QApplication.clipboard().setText(self._text)
        self.copy_button.setText("已复制")

    def enterEvent(self, event):
        super().enterEvent(event)
        self.copy_button.set_revealed(True)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._focus_changed(None, QApplication.focusWidget())

    @pyqtSlot(QWidget, QWidget)
    def _focus_changed(self, _old, current):
        focused = current is self or (current is not None and self.isAncestorOf(current))
        self.copy_button.set_revealed(self.underMouse() or focused)

    def set_history_highlight(self, highlighted: bool) -> None:
        self._location_label.setVisible(highlighted)

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
