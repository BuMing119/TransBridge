"""Palette-driven background-task monitor."""

from __future__ import annotations

from collections.abc import Callable
import time

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.components import ElidedLabel, reserve_text_width
from transbridge.ui.foundation.tabler_icons import tabler_icon

from .theme_support import BUTTON_STRUCTURE_STYLE, CARD_STRUCTURE_STYLE, SmartAssistantTheme

_STATUS_LABELS = {
    "running": "运行中",
    "completed": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
    "paused": "已暂停",
}
# Import compatibility for callers that only enumerate the historical mapping.
# Values are semantic domain-state keys now; visual colours live in DomainTokens.
_STATUS_COLORS = {status: status for status in _STATUS_LABELS}


class _TaskCard(QFrame):
    def __init__(
        self,
        task_id: str,
        task_data: dict,
        parent=None,
        *,
        action_callback: Callable[[str, str], None] | None = None,
        theme: SmartAssistantTheme | None = None,
    ):
        super().__init__(parent)
        self._task_id = task_id
        self._task_data = task_data
        self._status = task_data.get("status", "running")
        self._theme = theme or SmartAssistantTheme()
        self._action_callback = action_callback or (lambda _task_id, _action: None)
        self.setObjectName("TaskCard")
        self.setProperty("tbSurface", "card")
        self.setStyleSheet(CARD_STRUCTURE_STYLE)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMaximumHeight(80)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)
        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)
        top_row = QHBoxLayout()
        name = str(task_data.get("metadata", {}).get("type", task_data.get("metadata", {}).get("name", "后台任务")))
        self._name_label = ElidedLabel(name)
        self._name_label.setToolTip(name)
        self._name_label.setAccessibleDescription(name)
        self._status_label = QLabel(_STATUS_LABELS.get(self._status, self._status))
        top_row.addWidget(self._name_label, 1)
        top_row.addWidget(self._status_label)
        info_layout.addLayout(top_row)
        self._progress_bar = QProgressBar()
        self._progress_bar.setMaximumHeight(14)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("%v/%m")
        progress = task_data.get("progress", {})
        current, total = progress.get("current", 0), progress.get("total", 0)
        if total > 0:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(min(current, total))
        else:
            self._progress_bar.setRange(0, 0)
        self._progress_bar.setVisible(self._status in ("running", "paused"))
        detail = self._build_detail(progress)
        self._detail_label = ElidedLabel(detail)
        self._detail_label.setToolTip(detail)
        self._detail_label.setAccessibleDescription(detail)
        created = task_data.get("created_at", 0)
        elapsed = time.time() - created if created else 0
        self._elapsed_label = QLabel(self._format_duration(int(elapsed)))
        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self._progress_bar, stretch=2)
        bottom_row.addWidget(self._detail_label, stretch=1)
        bottom_row.addWidget(self._elapsed_label)
        info_layout.addLayout(bottom_row)
        layout.addLayout(info_layout, stretch=1)
        button_layout = QVBoxLayout()
        self._action_buttons: list[tuple[QPushButton, str]] = []
        actions = {
            "running": (("暂停", "pause", "warning"), ("取消", "cancel", "error")),
            "paused": (("恢复", "resume", "success"), ("取消", "cancel", "error")),
            "completed": (("清除", "cleanup", "muted"),),
            "failed": (("清除", "cleanup", "muted"),),
            "cancelled": (("清除", "cleanup", "muted"),),
        }.get(self._status, ())
        for text, action, state in actions:
            button = QPushButton(text)
            button.setAccessibleName(f"{text}任务 {name}")
            button.setProperty("tbActionState", state)
            button.setStyleSheet(BUTTON_STRUCTURE_STYLE)
            reserve_text_width(button, ("暂停", "恢复", "取消", "清除"))
            button.clicked.connect(lambda _checked, value=action: self._emit_action(value))
            self._action_buttons.append((button, state))
            button_layout.addWidget(button)
        layout.addLayout(button_layout)
        self.apply_theme(self._theme)

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme
        theme.apply_domain(self, "task", self._status, background=True)
        theme.apply_semantic(self._name_label, "default")
        theme.apply_domain(self._status_label, "task", self._status, background=True)
        theme.apply_semantic(self._detail_label, "muted")
        theme.apply_semantic(self._elapsed_label, "muted")
        theme.apply_domain(self._progress_bar, "task", "running")
        for button, state in self._action_buttons:
            theme.apply_semantic(button, state, background=True)
        label = _STATUS_LABELS.get(self._status, self._status)
        theme.mark_status(self, f"{self._name_label.full_text}：{label}", self._status)

    def _emit_action(self, action: str) -> None:
        self._action_callback(self._task_id, action)

    @staticmethod
    def _build_detail(progress: dict) -> str:
        if not progress:
            return ""
        current, total = progress.get("current", 0), progress.get("total", 0)
        if current and total:
            return f"{current}/{total}"
        for key in ("entry_count", "message", "phase"):
            if key in progress:
                return str(progress[key])
        return ""

    @staticmethod
    def _format_duration(seconds: int) -> str:
        return f"{seconds}秒" if seconds < 60 else f"{seconds // 60}分{seconds % 60}秒"

    def update_elapsed(self) -> None:
        pass


class TaskMonitorWidget(QWidget):
    task_action = pyqtSignal(str, str)

    def __init__(self, parent=None, *, theme: SmartAssistantTheme | None = None):
        super().__init__(parent)
        self._theme = theme or SmartAssistantTheme()
        self._tasks: dict[str, dict] = {}
        self._cards: list[_TaskCard] = []
        self._collapsed = True
        self.setAccessibleName("后台任务监控")
        # 允许底部 Dock 较矮时退化为仅显示标题栏，避免把聊天输入区挤出
        # 可见范围；正常高度下 QSplitter 仍按 sizeHint 分配完整任务区域。
        self.setMinimumHeight(36)
        self.setMaximumHeight(36)
        self._init_ui()
        self._apply_collapsed_state()
        self.apply_theme(self._theme)

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        self._title_bar = QFrame()
        self._title_bar.setFixedHeight(36)
        title_layout = QHBoxLayout(self._title_bar)
        title_layout.setContentsMargins(8, 2, 8, 2)
        self._title_label = QLabel("后台任务 (0)")
        self._collapse_btn = QPushButton()
        self._collapse_btn.setAccessibleName("展开后台任务")
        self._clear_all_btn = QPushButton("清除已完成")
        self._clear_all_btn.setAccessibleName("清除已完成任务")
        for button in (self._collapse_btn, self._clear_all_btn):
            button.setStyleSheet(BUTTON_STRUCTURE_STYLE)
        self._collapse_btn.clicked.connect(self._toggle_collapse)
        self._clear_all_btn.clicked.connect(lambda: self.task_action.emit("__all__", "cleanup_completed"))
        title_layout.addWidget(self._title_label)
        title_layout.addStretch()
        title_layout.addWidget(self._clear_all_btn)
        title_layout.addWidget(self._collapse_btn)
        main_layout.addWidget(self._title_bar)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(4, 4, 4, 4)
        self._list_layout.addStretch()
        self._scroll.setWidget(self._list_widget)
        main_layout.addWidget(self._scroll, stretch=1)
        self._empty_label = QLabel("无后台任务")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._list_layout.insertWidget(0, self._empty_label)

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme
        for widget in (self, self._title_bar, self._scroll, self._list_widget):
            theme.apply_surface(widget, alternate=True)
        for widget in (self._title_label, self._empty_label, self._collapse_btn):
            theme.apply_semantic(widget, "muted")
        icon_name = "chevron-right" if self._collapsed else "chevron-left"
        self._collapse_btn.setIcon(tabler_icon(self._collapse_btn, icon_name, 15))
        theme.apply_semantic(self._clear_all_btn, "error")
        for card in self._cards:
            card.apply_theme(theme)

    def refresh(self, tasks: list[dict]) -> None:
        for card in self._cards:
            self._list_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()
        self._tasks = {}
        active_count = 0
        for task in tasks:
            task_id = task.get("task_id", "")
            status = task.get("status", "running")
            self._tasks[task_id] = task
            active_count += status in ("running", "paused")
            card = _TaskCard(
                task_id,
                task,
                action_callback=lambda value, action: self.task_action.emit(value, action),
                theme=self._theme,
            )
            self._cards.append(card)
            self._list_layout.insertWidget(self._list_layout.count() - 1, card)
        self._title_label.setText(f"后台任务 ({len(tasks)})")
        self._empty_label.setVisible(not tasks)
        self._clear_all_btn.setVisible(not self._collapsed and len(tasks) - active_count > 0)

    def update_task(self, task_id: str, status: str | None = None, progress: dict | None = None) -> None:
        pass

    def reset(self) -> None:
        self.refresh([])

    def _toggle_collapse(self) -> None:
        self._collapsed = not self._collapsed
        self._apply_collapsed_state()
        self.apply_theme(self._theme)

    def _apply_collapsed_state(self) -> None:
        self._scroll.setVisible(not self._collapsed)
        completed_count = sum(task.get("status") not in ("running", "paused") for task in self._tasks.values())
        self._clear_all_btn.setVisible(not self._collapsed and completed_count > 0)
        self._collapse_btn.setAccessibleName("展开后台任务" if self._collapsed else "折叠后台任务")
        self.setMinimumHeight(36 if self._collapsed else 140)
        self.setMaximumHeight(36 if self._collapsed else 300)
        self.updateGeometry()


__all__ = ["TaskMonitorWidget"]
