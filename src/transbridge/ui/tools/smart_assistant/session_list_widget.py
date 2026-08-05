"""SessionListWidget — 左侧可折叠会话列表栏。

ADR-008 D17: 纯 UI 组件，通过回调与 Panel 通信，不直接依赖 SessionManager。
数据通过 set_sessions() 注入，当前活跃会话通过 set_active() 设置。
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QPushButton, QLabel, QFrame, QMessageBox,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QEnterEvent


# ── 配色（与 ChatWidget 颜色面板一致）─────────────────────
_COLORS = {
    "bg": "#fafafa",
    "border": "#e0e0e0",
    "text_primary": "#333",
    "text_secondary": "#888",
    "hover_bg": "#f0f0f0",
    "active_bg": "#E3F2FD",
    "button_hover": "#e8e8e8",
    "danger": "#D32F2F",
    "danger_bg": "#FFEBEE",
}


class _SessionRow(QFrame):
    """单行会话条目。"""

    clicked = pyqtSignal(str)
    delete_requested = pyqtSignal(str)
    rename_requested = pyqtSignal(str, str)  # (session_id, new_name)

    def __init__(self, session_meta: dict, is_active: bool = False, parent=None):
        super().__init__(parent)
        self._sid = session_meta["session_id"]
        self._is_active = is_active
        self._hovered = False

        self.setFixedHeight(48)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self._update_style()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 4, 4)
        layout.setSpacing(4)

        # 左侧文本区
        text_layout = QVBoxLayout()
        text_layout.setSpacing(1)

        name = session_meta.get("name", "未命名")[:30]
        self._name_label = QLabel(name)
        self._name_label.setFont(QFont("Microsoft YaHei", 10, QFont.Weight.Bold))
        self._name_label.setStyleSheet(f"color: {_COLORS['text_primary']}; border: none; background: transparent;")

        count = session_meta.get("message_count", 0)
        time_str = self._format_time(session_meta.get("last_active_at", ""))
        subtitle = f"{count} 条消息 · {time_str}"
        self._sub_label = QLabel(subtitle)
        self._sub_label.setFont(QFont("Microsoft YaHei", 8))
        self._sub_label.setStyleSheet(f"color: {_COLORS['text_secondary']}; border: none; background: transparent;")

        text_layout.addWidget(self._name_label)
        text_layout.addWidget(self._sub_label)
        layout.addLayout(text_layout, 1)

        # 右侧"更多"按钮（悬停时显示，弹出重命名/删除菜单）
        self._menu_btn = QPushButton("⋯")
        self._menu_btn.setFixedSize(26, 26)
        self._menu_btn.setFont(QFont("Microsoft YaHei", 14, QFont.Weight.Bold))
        self._menu_btn.setStyleSheet(
            "QPushButton { color: #555; border: none; background: rgba(0,0,0,0.06);"
            " border-radius: 13px; }"
            "QPushButton:hover { background: rgba(0,0,0,0.15); color: #222; }"
            "QPushButton:pressed { background: rgba(0,0,0,0.25); }"
        )
        self._menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._menu_btn.setVisible(False)
        self._menu_btn.clicked.connect(self._on_menu)
        layout.addWidget(self._menu_btn)

    def _update_style(self):
        if self._is_active:
            bg = _COLORS["active_bg"]
            border = "#90CAF9"
        elif self._hovered:
            bg = _COLORS["hover_bg"]
            border = _COLORS["border"]
        else:
            bg = _COLORS["bg"]
            border = "transparent"
        self.setStyleSheet(
            f"SessionRow {{ background: {bg}; border-bottom: 1px solid {_COLORS['border']}; "
            f"border-left: 3px solid {border}; border-radius: 0px; }}"
        )

    def enterEvent(self, event: QEnterEvent | None):
        self._hovered = True
        self._menu_btn.setVisible(True)
        self._update_style()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._menu_btn.setVisible(False)
        self._update_style()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._sid)
        super().mousePressEvent(event)

    def _on_menu(self):
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: white; border: 1px solid #D0D0D0; border-radius: 6px;"
            " padding: 4px 0; }"
            "QMenu::item { padding: 6px 24px; font-size: 12px; color: #333; }"
            "QMenu::item:selected { background: #E3F2FD; color: #1565C0; }"
            "QMenu::separator { height: 1px; background: #E0E0E0; margin: 3px 8px; }"
        )
        rename_action = menu.addAction("重命名")
        menu.addSeparator()
        delete_action = menu.addAction("删除")
        delete_action.setData("delete")

        action = menu.exec(self._menu_btn.mapToGlobal(self._menu_btn.rect().bottomLeft()))
        if action == rename_action:
            self.rename_requested.emit(self._sid, self._name_label.text())
        elif action == delete_action:
            self.delete_requested.emit(self._sid)

    def set_active(self, active: bool):
        self._is_active = active
        self._update_style()

    @staticmethod
    def _format_time(iso_str: str) -> str:
        if not iso_str:
            return ""
        try:
            # ISO format: "2026-08-05T14:30:00" or "2026-08-05T14:30:00.123456"
            date_part = iso_str[:10]
            time_part = iso_str[11:16]
            today = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
            if date_part == today:
                return f"今天 {time_part}"
            return f"{date_part[5:]} {time_part}"  # "08-05 14:30"
        except (ValueError, IndexError):
            return iso_str[:16]


class SessionListWidget(QWidget):
    """可折叠会话列表栏。

    信号:
        create_session(name: str)
        switch_session(session_id: str)
        delete_session(session_id: str)
    """

    create_session = pyqtSignal(str)
    switch_session = pyqtSignal(str)
    delete_session = pyqtSignal(str)
    rename_session = pyqtSignal(str, str)  # (session_id, current_name)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._collapsed = False
        self._active_sid: str | None = None
        self._rows: dict[str, _SessionRow] = {}

        self.setMinimumWidth(40)
        self.setMaximumWidth(280)

        self._setup_ui()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── 头部栏：标题 + 折叠/新建按钮 ──
        header = QFrame()
        header.setFixedHeight(36)
        header.setStyleSheet(
            f"QFrame {{ background: {_COLORS['bg']}; "
            f"border-bottom: 1px solid {_COLORS['border']}; }}"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 0, 4, 0)

        title = QLabel("会话")
        title.setFont(QFont("Microsoft YaHei", 10, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {_COLORS['text_primary']}; border: none; background: transparent;")
        header_layout.addWidget(title)
        header_layout.addStretch()

        # 新建按钮
        add_btn = QPushButton("+")
        add_btn.setFixedSize(24, 24)
        add_btn.setFont(QFont("Microsoft YaHei", 14))
        add_btn.setStyleSheet(
            f"QPushButton {{ color: {_COLORS['text_secondary']}; border: none; background: transparent; "
            f"border-radius: 12px; }}"
            f"QPushButton:hover {{ background: {_COLORS['button_hover']}; color: {_COLORS['text_primary']}; }}"
        )
        add_btn.clicked.connect(self._on_create)
        header_layout.addWidget(add_btn)

        # 折叠按钮
        self._toggle_btn = QPushButton("◀")
        self._toggle_btn.setFixedSize(24, 24)
        self._toggle_btn.setFont(QFont("Microsoft YaHei", 10))
        self._toggle_btn.setStyleSheet(
            f"QPushButton {{ color: {_COLORS['text_secondary']}; border: none; background: transparent; "
            f"border-radius: 12px; }}"
            f"QPushButton:hover {{ background: {_COLORS['button_hover']}; }}"
        )
        self._toggle_btn.clicked.connect(self._toggle)
        header_layout.addWidget(self._toggle_btn)

        main_layout.addWidget(header)

        # ── 会话列表滚动区 ──
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ border: none; background: {_COLORS['bg']}; }}"
        )

        self._list_container = QWidget()
        self._list_container.setStyleSheet(f"background: {_COLORS['bg']};")
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(0)
        self._list_layout.addStretch()

        self._scroll.setWidget(self._list_container)
        main_layout.addWidget(self._scroll, 1)

    # ── 公开方法 ──────────────────────────────────────────

    def set_sessions(self, sessions: list[dict]):
        """替换整个会话列表。"""
        # 清除现有行
        for row in self._rows.values():
            row.deleteLater()
        self._rows.clear()

        # 移除旧控件（保留 stretch）
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 重建行
        for meta in sessions:
            sid = meta["session_id"]
            row = _SessionRow(meta, is_active=(sid == self._active_sid))
            row.clicked.connect(self._on_switch)
            row.delete_requested.connect(self._on_delete)
            row.rename_requested.connect(lambda sid, name: self.rename_session.emit(sid, name))
            self._rows[sid] = row
            self._list_layout.insertWidget(self._list_layout.count() - 1, row)

    def set_active(self, session_id: str | None):
        """设置当前活跃会话。"""
        self._active_sid = session_id
        for sid, row in self._rows.items():
            row.set_active(sid == session_id)

    def set_collapsed(self, collapsed: bool):
        self._collapsed = collapsed
        self._scroll.setVisible(not collapsed)
        self._toggle_btn.setText("▶" if collapsed else "◀")
        if collapsed:
            self.setMaximumWidth(40)
        else:
            self.setMaximumWidth(280)

    # ── 内部槽 ────────────────────────────────────────────

    def _toggle(self):
        self.set_collapsed(not self._collapsed)

    def _on_create(self):
        self.create_session.emit("新对话")

    def _on_switch(self, session_id: str):
        self.switch_session.emit(session_id)

    def _on_delete(self, session_id: str):
        meta = self._rows.get(session_id)
        name = meta._name_label.text() if meta else session_id
        reply = QMessageBox.question(
            self, "删除会话",
            f"确定要删除会话「{name}」吗？\n此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.delete_session.emit(session_id)
