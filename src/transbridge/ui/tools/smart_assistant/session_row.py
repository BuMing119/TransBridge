"""Keyboard-accessible assistant conversation row and its local actions."""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QEnterEvent
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QVBoxLayout

from transbridge.ui.foundation.components import ElidedLabel
from transbridge.ui.foundation.tabler_icons import tabler_icon

from .theme_support import (
    SESSION_ROW_STRUCTURE_STYLE,
    SIDEBAR_BUTTON_STYLE,
    TRANSPARENT_STRUCTURE_STYLE,
    SmartAssistantTheme,
)


def session_display_name(name: str) -> str:
    return "新对话" if name == "New conversation" else name


class SessionRow(QFrame):
    clicked = pyqtSignal(str)
    delete_requested = pyqtSignal(str)
    rename_requested = pyqtSignal(str, str)
    move_requested = pyqtSignal(str, int)

    def __init__(
        self,
        session_meta: dict,
        is_active: bool = False,
        parent=None,
        *,
        theme: SmartAssistantTheme | None = None,
        outside_workbench: bool = False,
    ):
        super().__init__(parent)
        self._theme = theme or SmartAssistantTheme()
        self._sid = session_meta["session_id"]
        self.name = str(session_meta.get("name", "未命名"))
        display_name = session_display_name(self.name)
        self._bound = bool(session_meta.get("project_id") and session_meta.get("variant_id"))
        self._is_active = is_active
        self._hovered = False
        self._outside_workbench = outside_workbench
        self.setMinimumHeight(60)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        self.setObjectName("smartAssistantSession")
        self.setStyleSheet(SESSION_ROW_STRUCTURE_STYLE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 6, 8)
        layout.setSpacing(4)
        text_layout = QVBoxLayout()
        text_layout.setSpacing(3)
        name = self.name
        self._name_label = ElidedLabel(display_name)
        self._name_label.setToolTip(name)
        self._name_label.setStyleSheet(TRANSPARENT_STRUCTURE_STYLE)
        count = session_meta.get("message_count", 0)
        timestamp = self._format_time(session_meta.get("last_active_at", ""))
        text_layout.addWidget(self._name_label)
        variant = session_meta.get("variant_name") or session_meta.get("variant_id")
        self._variant_label = ElidedLabel(f"版本：{variant}" if variant else "")
        self._details = " · ".join(
            filter(None, (f"版本：{variant}" if variant else "未绑定工程", f"{count} 条消息", timestamp))
        )
        self._variant_label.setToolTip(self._details)
        self._variant_label.setStyleSheet(TRANSPARENT_STRUCTURE_STYLE)
        metadata = QHBoxLayout()
        metadata.setSpacing(6)
        metadata.addWidget(self._variant_label, 1)
        self._scope_label = QLabel("仅浏览", self)
        self._scope_label.setStyleSheet(
            "QLabel { border: none; border-radius: 4px; padding: 1px 4px; "
            "background: palette(midlight); color: palette(shadow); }"
        )
        self._scope_label.setVisible(outside_workbench)
        metadata.addWidget(self._scope_label)
        text_layout.addLayout(metadata)
        layout.addLayout(text_layout, 1)
        if not variant:
            self._variant_label.set_full_text(timestamp or "未绑定工程")
        self._menu_btn = QPushButton()
        self._menu_btn.setAccessibleName(f"会话 {name} 的更多操作")
        self._menu_btn.setFixedSize(26, 26)
        self._menu_btn.setStyleSheet(SIDEBAR_BUTTON_STYLE)
        self._menu_btn.setToolTip("会话操作（F2 重命名，Delete 删除）")
        self._menu_btn.clicked.connect(self._on_menu)
        layout.addWidget(self._menu_btn)
        self.apply_theme(self._theme)

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme
        if self.property("active") != self._is_active:
            self.setProperty("active", self._is_active)
            self.style().unpolish(self)
            self.style().polish(self)
        self.setProperty("hovered", self._hovered)
        theme.apply_navigation_item(self, selected=self._is_active, hovered=self._hovered or self.hasFocus())
        self.setProperty("outsideWorkbench", self._outside_workbench)
        theme.apply_semantic(
            self._name_label, "muted" if self._outside_workbench and not self._is_active else "default"
        )
        theme.apply_semantic(self._variant_label, "muted")
        theme.apply_semantic(self._scope_label, "muted")
        theme.apply_semantic(self._menu_btn, "muted")
        self._name_label.setStyleSheet(
            TRANSPARENT_STRUCTURE_STYLE + ("font-weight: 600;" if self._is_active else "font-weight: 400;")
        )
        self._menu_btn.setIcon(tabler_icon(self._menu_btn, "dots", 15))
        status = "正在浏览的会话" if self._is_active else "会话"
        self.setAccessibleName(f"{status}：{session_display_name(self.name)}")
        scope_hint = ""
        if self._outside_workbench:
            scope_hint = (
                "仅浏览；继续操作前需切换到该工程和版本"
                if self._bound
                else "仅浏览；未绑定工程与版本，请在目标工程中新建对话"
            )
        self.setToolTip("\n".join(filter(None, (self.name, self._details, scope_hint))))
        self.setAccessibleDescription("；".join(filter(None, (self._details, scope_hint))))

    def enterEvent(self, event: QEnterEvent | None) -> None:
        self._hovered = True
        self.apply_theme(self._theme)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.apply_theme(self._theme)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._sid)
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit(self._sid)
        elif key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.move_requested.emit(self._sid, -1 if key == Qt.Key.Key_Up else 1)
        elif key == Qt.Key.Key_F2:
            self.rename_requested.emit(self._sid, self.name)
        elif key == Qt.Key.Key_Delete:
            self.delete_requested.emit(self._sid)
        elif key == Qt.Key.Key_Menu or (
            key == Qt.Key.Key_F10 and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self._on_menu()
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def focusInEvent(self, event) -> None:
        self.apply_theme(self._theme)
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        self.apply_theme(self._theme)
        super().focusOutEvent(event)

    def _on_menu(self) -> None:
        menu = QMenu(self)
        rename_action = menu.addAction("重命名")
        menu.addSeparator()
        delete_action = menu.addAction("删除")
        action = menu.exec(self._menu_btn.mapToGlobal(self._menu_btn.rect().bottomLeft()))
        if action == rename_action:
            self.rename_requested.emit(self._sid, self.name)
        elif action == delete_action:
            self.delete_requested.emit(self._sid)

    def set_active(self, active: bool) -> None:
        self._is_active = active
        self.apply_theme(self._theme)

    @staticmethod
    def _format_time(iso_str: str) -> str:
        if not iso_str:
            return ""
        try:
            date_part, time_part = iso_str[:10], iso_str[11:16]
            return (
                f"今天 {time_part}"
                if date_part == datetime.now().strftime("%Y-%m-%d")
                else f"{date_part[5:]} {time_part}"
            )
        except (ValueError, IndexError):
            return iso_str[:16]
