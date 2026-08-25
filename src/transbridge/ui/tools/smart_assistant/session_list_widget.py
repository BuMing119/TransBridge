"""Palette-driven, collapsible Smart Assistant session list."""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QEnterEvent, QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.tabler_icons import tabler_icon

from .theme_support import BUTTON_STRUCTURE_STYLE, TRANSPARENT_STRUCTURE_STYLE, SmartAssistantTheme


class _SessionRow(QFrame):
    clicked = pyqtSignal(str)
    delete_requested = pyqtSignal(str)
    rename_requested = pyqtSignal(str, str)

    def __init__(
        self,
        session_meta: dict,
        is_active: bool = False,
        parent=None,
        *,
        theme: SmartAssistantTheme | None = None,
    ):
        super().__init__(parent)
        self._theme = theme or SmartAssistantTheme()
        self._sid = session_meta["session_id"]
        self._is_active = is_active
        self._hovered = False
        self.setFixedHeight(84)
        self.setProperty("tbSurface", "card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 8, 10)
        layout.setSpacing(8)
        text_layout = QVBoxLayout()
        text_layout.setSpacing(4)
        name = session_meta.get("name", "未命名")[:30]
        self._name_label = QLabel(name)
        self._name_label.setFont(QFont("Microsoft YaHei", 10, QFont.Weight.Bold))
        self._name_label.setStyleSheet(TRANSPARENT_STRUCTURE_STYLE)
        count = session_meta.get("message_count", 0)
        self._sub_label = QLabel(f"{count} 条消息 · {self._format_time(session_meta.get('last_active_at', ''))}")
        self._sub_label.setFont(QFont("Microsoft YaHei", 8))
        self._sub_label.setStyleSheet(TRANSPARENT_STRUCTURE_STYLE)
        text_layout.addWidget(self._name_label)
        text_layout.addWidget(self._sub_label)
        layout.addLayout(text_layout, 1)
        self._menu_btn = QPushButton()
        self._menu_btn.setAccessibleName(f"会话 {name} 的更多操作")
        self._menu_btn.setFixedSize(26, 26)
        self._menu_btn.setStyleSheet(BUTTON_STRUCTURE_STYLE)
        self._menu_btn.setVisible(False)
        self._menu_btn.clicked.connect(self._on_menu)
        layout.addWidget(self._menu_btn)
        self.apply_theme(self._theme)

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme
        self.setProperty("active", self._is_active)
        self.setProperty("hovered", self._hovered)
        theme.apply_surface(self, alternate=self._is_active or self._hovered)
        theme.apply_semantic(self._name_label, "default")
        theme.apply_semantic(self._sub_label, "muted")
        theme.apply_semantic(self._menu_btn, "muted", background=True)
        self._menu_btn.setIcon(tabler_icon(self._menu_btn, "dots", 15))
        status = "当前会话" if self._is_active else "会话"
        self.setAccessibleName(f"{status}：{self._name_label.text()}")
        self.setAccessibleDescription(self._sub_label.text())

    def enterEvent(self, event: QEnterEvent | None) -> None:
        self._hovered = True
        self._menu_btn.setVisible(True)
        self.apply_theme(self._theme)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self._menu_btn.setVisible(False)
        self.apply_theme(self._theme)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._sid)
        super().mousePressEvent(event)

    def _on_menu(self) -> None:
        menu = QMenu(self)
        rename_action = menu.addAction("重命名")
        menu.addSeparator()
        delete_action = menu.addAction("删除")
        action = menu.exec(self._menu_btn.mapToGlobal(self._menu_btn.rect().bottomLeft()))
        if action == rename_action:
            self.rename_requested.emit(self._sid, self._name_label.text())
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


class SessionListWidget(QWidget):
    create_session = pyqtSignal(str)
    switch_session = pyqtSignal(str)
    delete_session = pyqtSignal(str)
    rename_session = pyqtSignal(str, str)

    def __init__(self, parent=None, *, theme: SmartAssistantTheme | None = None):
        super().__init__(parent)
        self._theme = theme or SmartAssistantTheme()
        self._collapsed = False
        self._active_sid: str | None = None
        self._rows: dict[str, _SessionRow] = {}
        self._session_data: list[dict] = []
        self.setAccessibleName("会话列表")
        self.setMinimumWidth(220)
        self.setMaximumWidth(260)
        self._setup_ui()
        self.apply_theme(self._theme)

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        self._header = QFrame()
        self._header.setFixedHeight(52)
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(14, 0, 10, 0)
        self._title = QLabel("会话")
        self._title.setFont(QFont("Microsoft YaHei", 10, QFont.Weight.Bold))
        header_layout.addWidget(self._title)
        header_layout.addStretch()
        self._search_btn = QPushButton()
        self._search_btn.setAccessibleName("搜索会话")
        self._search_btn.setToolTip("搜索会话")
        self._add_btn = QPushButton()
        self._add_btn.setAccessibleName("新建会话")
        for button in (self._search_btn, self._add_btn):
            button.setFixedSize(24, 24)
            button.setStyleSheet(BUTTON_STRUCTURE_STYLE)
            header_layout.addWidget(button)
        self._search_btn.clicked.connect(self._toggle_search)
        self._add_btn.clicked.connect(self._on_create)
        main_layout.addWidget(self._header)

        self._search_input = QLineEdit()
        self._search_input.setAccessibleName("会话搜索输入框")
        self._search_input.setPlaceholderText("搜索会话")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setContentsMargins(10, 4, 10, 4)
        self._search_input.setVisible(False)
        self._search_input.textChanged.connect(self._apply_filter)
        main_layout.addWidget(self._search_input)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(10, 8, 10, 8)
        self._list_layout.setSpacing(10)
        self._list_layout.addStretch()
        self._scroll.setWidget(self._list_container)
        main_layout.addWidget(self._scroll, 1)

        self._toggle_btn = QPushButton("收起侧栏")
        self._toggle_btn.setAccessibleName("折叠会话列表")
        self._toggle_btn.setFixedHeight(42)
        self._toggle_btn.setStyleSheet(BUTTON_STRUCTURE_STYLE)
        self._toggle_btn.clicked.connect(self._toggle)
        main_layout.addWidget(self._toggle_btn)

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme
        for widget in (self, self._header, self._scroll, self._list_container):
            theme.apply_surface(widget, alternate=True)
        theme.apply_surface(self._search_input)
        theme.apply_semantic(self._title, "default")
        for button in (self._search_btn, self._add_btn, self._toggle_btn):
            theme.apply_semantic(button, "muted", background=True)
        self._search_btn.setIcon(tabler_icon(self._search_btn, "search", 15))
        self._add_btn.setIcon(tabler_icon(self._add_btn, "plus", 15))
        collapse_icon = "chevron-right" if self._collapsed else "arrow-left"
        self._toggle_btn.setIcon(tabler_icon(self._toggle_btn, collapse_icon, 15))
        for row in self._rows.values():
            row.apply_theme(theme)

    def set_sessions(self, sessions: list[dict]) -> None:
        self._session_data = list(sessions)
        self._rebuild_rows(self._filtered_sessions())

    def _rebuild_rows(self, sessions: list[dict]) -> None:
        for row in self._rows.values():
            row.deleteLater()
        self._rows.clear()
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for meta in sessions:
            sid = meta["session_id"]
            row = _SessionRow(meta, is_active=sid == self._active_sid, theme=self._theme)
            row.clicked.connect(self._on_switch)
            row.delete_requested.connect(self._on_delete)
            row.rename_requested.connect(lambda row_sid, name: self.rename_session.emit(row_sid, name))
            self._rows[sid] = row
            self._list_layout.insertWidget(self._list_layout.count() - 1, row)

    def _filtered_sessions(self) -> list[dict]:
        query = self._search_input.text().strip().casefold()
        if not query:
            return list(self._session_data)
        return [meta for meta in self._session_data if query in str(meta.get("name", "")).casefold()]

    def _apply_filter(self) -> None:
        self._rebuild_rows(self._filtered_sessions())

    def _toggle_search(self) -> None:
        visible = not self._search_input.isVisible()
        self._search_input.setVisible(visible)
        if visible:
            self._search_input.setFocus()

    def set_active(self, session_id: str | None) -> None:
        self._active_sid = session_id
        for sid, row in self._rows.items():
            row.set_active(sid == session_id)

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self._scroll.setVisible(not collapsed)
        self._header.setVisible(not collapsed)
        self._search_input.setVisible(False if collapsed else bool(self._search_input.text()))
        self._toggle_btn.setText("" if collapsed else "收起侧栏")
        self._toggle_btn.setAccessibleName("展开会话列表" if collapsed else "折叠会话列表")
        self.setMinimumWidth(48 if collapsed else 220)
        self.setMaximumWidth(48 if collapsed else 260)
        self.apply_theme(self._theme)

    def _toggle(self) -> None:
        self.set_collapsed(not self._collapsed)

    def _on_create(self) -> None:
        self.create_session.emit("新对话")

    def _on_switch(self, session_id: str) -> None:
        self.switch_session.emit(session_id)

    def _on_delete(self, session_id: str) -> None:
        row = self._rows.get(session_id)
        name = row._name_label.text() if row else session_id
        reply = QMessageBox.question(
            self,
            "删除会话",
            f"确定要删除会话「{name}」吗？\n此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.delete_session.emit(session_id)


__all__ = ["SessionListWidget"]
