"""Collapsible thinking indicator with theme-owned presentation."""

from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget

from .theme_support import BUTTON_STRUCTURE_STYLE, TRANSPARENT_STRUCTURE_STYLE, SmartAssistantTheme


class ThinkingIndicator(QWidget):
    _ANIM_DOTS = [".  ", ".. ", "..."]

    def __init__(self, parent=None, *, theme: SmartAssistantTheme | None = None):
        super().__init__(parent)
        self._theme = theme or SmartAssistantTheme()
        self._expanded = False
        self._anim_idx = 0
        self._thought_text = ""
        self.setProperty("tbSurface", "card")
        self.setAccessibleName("思考状态")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(6)
        self._icon = QLabel("")
        self._label = QLabel("正在思考中")
        self._dots = QLabel(self._ANIM_DOTS[0])
        self._dots.setMinimumWidth(28)
        for label in (self._icon, self._label, self._dots):
            label.setStyleSheet(TRANSPARENT_STRUCTURE_STYLE)
            row.addWidget(label)
        row.addStretch()
        self._toggle_btn = QPushButton("展开")
        self._toggle_btn.setAccessibleName("展开思考过程")
        self._toggle_btn.setStyleSheet(BUTTON_STRUCTURE_STYLE)
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.clicked.connect(self.toggle_expand)
        row.addWidget(self._toggle_btn)
        layout.addLayout(row)
        self._detail = QTextEdit()
        self._detail.setAccessibleName("思考过程详情")
        self._detail.setReadOnly(True)
        self._detail.setMaximumHeight(200)
        self._detail.setVisible(False)
        layout.addWidget(self._detail)
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._anim_tick)
        self.apply_theme(self._theme)

    @property
    def thought_text(self) -> str:
        return self._thought_text

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme
        theme.apply_semantic(self, "muted", background=True)
        for widget in (self._icon, self._label, self._dots, self._detail, self._toggle_btn):
            theme.apply_semantic(widget, "muted")
        theme.mark_status(self, "正在思考" if not self._expanded else "思考过程已展开", "running")

    def set_thought(self, text: str) -> None:
        self._thought_text = text
        self._detail.setPlainText(text)
        self._anim_idx = 0
        self._dots.setText(self._ANIM_DOTS[0])
        self._timer.start()
        self.show()

    def toggle_expand(self) -> None:
        self._expanded = not self._expanded
        self._detail.setVisible(self._expanded)
        self._toggle_btn.setText("收起" if self._expanded else "展开")
        self._toggle_btn.setAccessibleName("收起思考过程" if self._expanded else "展开思考过程")
        self._label.setText("思考过程" if self._expanded else "正在思考中")
        self._dots.setText("" if self._expanded else self._ANIM_DOTS[0])
        if self._expanded:
            self._timer.stop()
        else:
            self._timer.start()
        self.apply_theme(self._theme)

    def stop_animation(self) -> None:
        self._timer.stop()

    def clear(self) -> None:
        self._timer.stop()
        self.hide()

    def _anim_tick(self) -> None:
        if sip.isdeleted(self._dots):
            self._timer.stop()
            return
        self._anim_idx = (self._anim_idx + 1) % len(self._ANIM_DOTS)
        self._dots.setText(self._ANIM_DOTS[self._anim_idx])


__all__ = ["ThinkingIndicator"]
