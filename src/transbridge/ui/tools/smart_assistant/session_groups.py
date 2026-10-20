"""Collapsible project sections for browsing assistant conversations."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QTransform
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from transbridge.ui.foundation.components import ElidedLabel
from transbridge.ui.foundation.tabler_icons import tabler_icon

from .theme_support import SIDEBAR_BUTTON_STYLE, TRANSPARENT_STRUCTURE_STYLE, SmartAssistantTheme


class SessionGroup(QWidget):
    """Expanding a project only reveals rows; selecting a row is a separate action."""

    expansion_changed = pyqtSignal(object, bool)

    def __init__(
        self,
        project_id: str | None,
        name: str,
        count: int,
        *,
        current: bool,
        expanded: bool,
        theme: SmartAssistantTheme,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.project_id = project_id
        self.name = name
        self._current = current
        self._theme = theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.header = QPushButton()
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.setStyleSheet(SIDEBAR_BUTTON_STYLE)
        self.header.setMinimumHeight(36)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(4, 6, 6, 6)
        header_layout.setSpacing(6)
        self._arrow_label = QLabel()
        self._arrow_label.setFixedSize(12, 12)
        self._folder_label = QLabel()
        self._folder_label.setFixedSize(16, 16)
        self._name_label = ElidedLabel(name)
        self._count_label = QLabel(str(count))
        self._current_label = QLabel("当前")
        for label in (self._arrow_label, self._folder_label, self._name_label, self._count_label, self._current_label):
            label.setStyleSheet(TRANSPARENT_STRUCTURE_STYLE)
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._current_label.setStyleSheet(
            "QLabel { border: none; border-radius: 4px; padding: 1px 4px; "
            "background: palette(highlight); color: palette(link); }"
        )
        self._name_label.setStyleSheet(TRANSPARENT_STRUCTURE_STYLE + "font-weight: 600;")
        header_layout.addWidget(self._arrow_label)
        header_layout.addWidget(self._folder_label)
        header_layout.addWidget(self._name_label, 1)
        header_layout.addWidget(self._count_label)
        header_layout.addWidget(self._current_label)
        self._current_label.setVisible(current)
        self._count_label.setVisible(not current)
        current_label = "；当前工程" if current else ""
        self.header.setAccessibleName(f"{name}，{count} 个对话{current_label}")
        self.header.setToolTip(f"{name}{current_label}；展开或收起对话，不切换工作台")
        layout.addWidget(self.header)
        self.body = QWidget()
        self.rows_layout = QVBoxLayout(self.body)
        self.rows_layout.setContentsMargins(14, 0, 0, 0)
        self.rows_layout.setSpacing(3)
        layout.addWidget(self.body)
        self.header.toggled.connect(self._on_toggled)
        self._set_expanded(expanded)

    @property
    def expanded(self) -> bool:
        return self.header.isChecked()

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme
        for widget in (self, self.body, self.header):
            theme.apply_surface(widget, alternate=True)
            widget.setAutoFillBackground(False)
        theme.apply_semantic(self._name_label, "default" if self._current else "muted")
        theme.apply_semantic(self._count_label, "muted")
        theme.apply_semantic(self._current_label, "info")
        self._folder_label.setPixmap(tabler_icon(self.header, "folder", 16).pixmap(16, 16))
        pixmap = tabler_icon(self.header, "chevron-right", 12).pixmap(12, 12)
        if self.expanded:
            pixmap = pixmap.transformed(QTransform().rotate(90), Qt.TransformationMode.SmoothTransformation)
        self._arrow_label.setPixmap(pixmap)

    def _set_expanded(self, expanded: bool) -> None:
        self.body.setVisible(expanded)
        self.header.setAccessibleDescription("已展开；按空格收起" if expanded else "已收起；按空格展开")
        self.apply_theme(self._theme)

    def _on_toggled(self, expanded: bool) -> None:
        self._set_expanded(expanded)
        self.expansion_changed.emit(self.project_id, expanded)
