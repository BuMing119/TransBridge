"""Compact, theme-aware notice for a conversation outside the active workbench."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget

from transbridge.ui.foundation.tabler_icons import tabler_icon

from .theme_support import SmartAssistantTheme


class SessionScopeNotice(QWidget):
    def __init__(self, *, theme: SmartAssistantTheme | None = None):
        super().__init__()
        self._theme = theme or SmartAssistantTheme()
        self._state = "info"
        self._icon_name = "info-circle"
        self.setAccessibleName("会话工程状态")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 0)
        self._surface = QFrame(self)
        self._surface.setObjectName("sessionScopeNotice")
        outer.addWidget(self._surface)
        row = QHBoxLayout(self._surface)
        row.setContentsMargins(12, 9, 12, 9)
        row.setSpacing(8)
        self._icon = QLabel()
        self._icon.setFixedSize(17, 17)
        self._title = QLabel()
        self._title.setTextFormat(Qt.TextFormat.PlainText)
        self._title.setStyleSheet("font-weight: 600;")
        self._title.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self._detail = QLabel()
        self._detail.setTextFormat(Qt.TextFormat.PlainText)
        self._detail.setWordWrap(True)
        self._detail.setMinimumWidth(0)
        row.addWidget(self._icon, alignment=Qt.AlignmentFlag.AlignTop)
        row.addWidget(self._title, alignment=Qt.AlignmentFlag.AlignTop)
        row.addWidget(self._detail, 1)
        self.apply_theme(self._theme)
        self.hide()

    def show_notice(self, title, detail, *, state="info", icon="info-circle", kind="preview"):
        self._title.setText(title)
        self._detail.setText(detail)
        self._state, self._icon_name = state, icon
        self.setProperty("scopeState", kind)
        self.setAccessibleDescription(self.text())
        self.apply_theme(self._theme)
        self.show()

    def clear(self):
        self._title.clear()
        self._detail.clear()
        self.setProperty("scopeState", "matched")
        self.setAccessibleDescription("")
        self.hide()

    def text(self):
        return " · ".join(part for part in (self._title.text(), self._detail.text()) if part)

    def apply_theme(self, theme: SmartAssistantTheme):
        self._theme = theme
        theme.apply_surface(self)
        self.setAutoFillBackground(False)
        theme.apply_scope_notice(self._surface)
        theme.apply_semantic(self._icon, self._state)
        theme.apply_semantic(self._title, self._state)
        theme.apply_semantic(self._detail, "default")
        self._icon.setPixmap(tabler_icon(self._icon, self._icon_name, 17).pixmap(17, 17))
