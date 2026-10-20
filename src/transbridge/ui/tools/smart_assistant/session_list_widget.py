"""Palette-driven, collapsible Smart Assistant session list."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.tabler_icons import tabler_icon

from .session_groups import SessionGroup
from .session_row import SessionRow, session_display_name
from .theme_support import (
    SIDEBAR_BUTTON_STYLE,
    SIDEBAR_STRUCTURE_STYLE,
    SmartAssistantTheme,
)


class SessionListWidget(QWidget):
    COMPACT_WIDTH = 48

    create_session = pyqtSignal(str)
    switch_session = pyqtSignal(str)
    delete_session = pyqtSignal(str)
    rename_session = pyqtSignal(str, str)

    def __init__(self, parent=None, *, theme: SmartAssistantTheme | None = None):
        super().__init__(parent)
        self._theme = theme or SmartAssistantTheme()
        self._collapsed = False
        self._active_sid: str | None = None
        self._rows: dict[str, SessionRow] = {}
        self._groups: dict[str | None, SessionGroup] = {}
        self._expanded_projects: dict[str | None, bool] = {}
        self._workbench_project_id: str | None = None
        self._workbench_variant_id: str | None = None
        self._session_data: list[dict] = []
        self.setAccessibleName("会话列表")
        self.setObjectName("smartAssistantSessions")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(SIDEBAR_STRUCTURE_STYLE)
        self.setMinimumWidth(180)
        self.setMaximumWidth(260)
        self._setup_ui()
        self.apply_theme(self._theme)

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 1, 0)
        main_layout.setSpacing(0)
        self._header = QFrame()
        self._header.setFixedHeight(52)
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(14, 0, 10, 0)
        self._title = QLabel("会话")
        self._title.setStyleSheet("QLabel { font-weight: 600; }")
        header_layout.addWidget(self._title)
        header_layout.addStretch()
        self._search_btn = QPushButton()
        self._search_btn.setAccessibleName("搜索会话")
        self._search_btn.setToolTip("搜索会话")
        self._add_btn = QPushButton()
        self._add_btn.setAccessibleName("新建会话")
        for button in (self._search_btn, self._add_btn):
            button.setFixedSize(28, 28)
            button.setStyleSheet(SIDEBAR_BUTTON_STYLE)
            header_layout.addWidget(button)
        self._search_btn.clicked.connect(self._toggle_search)
        self._add_btn.clicked.connect(self._on_create)
        main_layout.addWidget(self._header)

        self._compact_navigation = QWidget()
        compact_layout = QVBoxLayout(self._compact_navigation)
        compact_layout.setContentsMargins(6, 8, 6, 8)
        compact_layout.setSpacing(8)
        self.compact_add_button = QToolButton()
        self.compact_sessions_button = QToolButton()
        for button, label in (
            (self.compact_add_button, "新建会话"),
            (self.compact_sessions_button, "查看会话"),
        ):
            button.setAccessibleName(label)
            button.setToolTip(label)
            button.setFixedSize(36, 36)
            button.setStyleSheet(SIDEBAR_BUTTON_STYLE)
            compact_layout.addWidget(button)
        compact_layout.addStretch(1)
        self.compact_add_button.clicked.connect(self._on_create)
        self.compact_sessions_button.clicked.connect(self._toggle)
        self._compact_navigation.hide()
        main_layout.addWidget(self._compact_navigation, 1)

        self._search_input = QLineEdit()
        self._search_input.setAccessibleName("会话搜索输入框")
        self._search_input.setPlaceholderText("搜索会话")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setContentsMargins(10, 4, 10, 4)
        self._search_input.setVisible(False)
        self._search_input.textChanged.connect(self._apply_filter)
        main_layout.addWidget(self._search_input)

        self._scroll = QScrollArea()
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(10, 8, 10, 8)
        self._list_layout.setSpacing(16)
        self._list_layout.addStretch()
        self._scroll.setWidget(self._list_container)
        main_layout.addWidget(self._scroll, 1)

        self._toggle_btn = QPushButton("收起侧栏")
        self._toggle_btn.setAccessibleName("折叠会话列表")
        self._toggle_btn.setFixedHeight(42)
        self._toggle_btn.setStyleSheet(SIDEBAR_BUTTON_STYLE)
        self._toggle_btn.clicked.connect(self._toggle)
        footer = QVBoxLayout()
        footer.setContentsMargins(8, 6, 8, 8)
        footer.addWidget(self._toggle_btn)
        main_layout.addLayout(footer)

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme
        theme.apply_session_sidebar(self)
        for widget in (self._header, self._scroll, self._list_container):
            theme.apply_surface(widget, alternate=True)
        theme.apply_surface(self._search_input)
        theme.apply_semantic(self._title, "default")
        for button in (self._search_btn, self._add_btn, self._toggle_btn):
            theme.apply_semantic(button, "muted")
            button.setAutoFillBackground(False)
        self._search_btn.setIcon(tabler_icon(self._search_btn, "search", 15))
        self._add_btn.setIcon(tabler_icon(self._add_btn, "plus", 15))
        for button, icon_name in (
            (self.compact_add_button, "plus"),
            (self.compact_sessions_button, "list-details"),
        ):
            theme.apply_surface(button, alternate=True)
            button.setIcon(tabler_icon(button, icon_name, 18))
        collapse_icon = "chevron-right" if self._collapsed else "arrow-left"
        self._toggle_btn.setIcon(tabler_icon(self._toggle_btn, collapse_icon, 15))
        for row in self._rows.values():
            row.apply_theme(theme)
        for group in self._groups.values():
            group.apply_theme(theme)

    def set_sessions(self, sessions: list[dict]) -> None:
        self._session_data = list(sessions)
        self._rebuild_rows(self._filtered_sessions())

    def set_workbench(self, project_id: str | None, variant_id: str | None) -> None:
        """Update the execution scope independently of the conversation being browsed."""
        if (project_id, variant_id) == (self._workbench_project_id, self._workbench_variant_id):
            return
        project_changed = project_id != self._workbench_project_id
        self._workbench_project_id = project_id
        self._workbench_variant_id = variant_id
        if project_changed:
            self._expanded_projects[project_id] = True
        self._rebuild_rows(self._filtered_sessions())

    def _rebuild_rows(self, sessions: list[dict]) -> None:
        focused = next(
            (
                (sid, row._menu_btn.hasFocus())
                for sid, row in self._rows.items()
                if row.hasFocus() or row._menu_btn.hasFocus()
            ),
            None,
        )
        focused_group = next((pid for pid, group in self._groups.items() if group.header.hasFocus()), None)
        group_had_focus = any(group.header.hasFocus() for group in self._groups.values())
        self._rows.clear()
        self._groups.clear()
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        grouped: dict[str | None, list[dict]] = {}
        for meta in sessions:
            grouped.setdefault(meta.get("project_id"), []).append(meta)
        ordered = sorted(grouped, key=lambda pid: (pid != self._workbench_project_id, pid is None))
        searching = bool(self._search_input.text().strip())
        for project_id in ordered:
            metadata = grouped[project_id]
            name = next((str(meta["project_name"]) for meta in metadata if meta.get("project_name")), project_id)
            group = SessionGroup(
                project_id,
                name or "未归属对话",
                len(metadata),
                current=project_id is not None and project_id == self._workbench_project_id,
                expanded=searching or self._expanded_projects.get(project_id, project_id == self._workbench_project_id),
                theme=self._theme,
            )
            group.expansion_changed.connect(self._remember_expansion)
            self._groups[project_id] = group
            self._list_layout.insertWidget(self._list_layout.count() - 1, group)
            self._add_rows(group, metadata)
            group.show()
        if focused is not None and focused[0] in self._rows:
            row = self._rows[focused[0]]
            if not row.parentWidget().isHidden():
                row.show()
                (row._menu_btn if focused[1] else row).setFocus()
        elif group_had_focus and focused_group in self._groups:
            self._groups[focused_group].header.setFocus()

    def _add_rows(self, group: SessionGroup, sessions: list[dict]) -> None:
        for meta in sessions:
            sid = meta["session_id"]
            outside_workbench = (meta.get("project_id"), meta.get("variant_id")) != (
                self._workbench_project_id,
                self._workbench_variant_id,
            )
            row = SessionRow(
                meta, is_active=sid == self._active_sid, theme=self._theme, outside_workbench=outside_workbench
            )
            row.clicked.connect(self._on_switch)
            row.delete_requested.connect(self._on_delete)
            row.rename_requested.connect(lambda row_sid, name: self.rename_session.emit(row_sid, name))
            row.move_requested.connect(self._move_focus)
            self._rows[sid] = row
            group.rows_layout.addWidget(row)

    def _remember_expansion(self, project_id: str | None, expanded: bool) -> None:
        if not self._search_input.text().strip():
            self._expanded_projects[project_id] = expanded

    def _move_focus(self, session_id: str, direction: int) -> None:
        identities = [sid for sid, row in self._rows.items() if not row.parentWidget().isHidden()]
        if session_id not in identities:
            return
        index = identities.index(session_id) + direction
        if 0 <= index < len(identities):
            row = self._rows[identities[index]]
            row.setFocus()
            self._scroll.ensureWidgetVisible(row)

    def _filtered_sessions(self) -> list[dict]:
        query = self._search_input.text().strip().casefold()
        if not query:
            return list(self._session_data)
        fields = ("name", "project_name", "project_id", "variant_name", "variant_id")
        return [
            meta
            for meta in self._session_data
            if query in session_display_name(str(meta.get("name") or "")).casefold()
            or any(query in str(meta.get(field) or "").casefold() for field in fields)
        ]

    def _apply_filter(self) -> None:
        self._rebuild_rows(self._filtered_sessions())

    def _toggle_search(self) -> None:
        visible = self._search_input.isHidden()
        self._search_input.setVisible(visible)
        if visible:
            self._search_input.setFocus()
        else:
            self._search_input.clear()
            self._search_btn.setFocus()

    def set_active(self, session_id: str | None) -> None:
        self._active_sid = session_id
        for sid, row in self._rows.items():
            row.set_active(sid == session_id)

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self._scroll.setVisible(not collapsed)
        self._header.setVisible(not collapsed)
        self._compact_navigation.setVisible(collapsed)
        self._search_input.setVisible(False if collapsed else bool(self._search_input.text()))
        self._toggle_btn.setText("" if collapsed else "收起侧栏")
        self._toggle_btn.setAccessibleName("展开会话列表" if collapsed else "折叠会话列表")
        self.setMinimumWidth(self.COMPACT_WIDTH if collapsed else 180)
        self.setMaximumWidth(self.COMPACT_WIDTH if collapsed else 260)
        self.apply_theme(self._theme)

    def _toggle(self) -> None:
        self.set_collapsed(not self._collapsed)

    def _on_create(self) -> None:
        self._search_input.clear()
        self.create_session.emit("新对话")

    def _on_switch(self, session_id: str) -> None:
        self.switch_session.emit(session_id)

    def _on_delete(self, session_id: str) -> None:
        row = self._rows.get(session_id)
        name = row.name if row else session_id
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
