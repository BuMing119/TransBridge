"""Thin Workbench views for summary filters and contextual workflow intents."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QWidget

from transbridge.ui.shell.action_catalog import IntentId
from transbridge.ui.workbench.workflow_presenter import (
    ContextActionViewState,
    StatisticsSummary,
)


class StatisticsSummaryView(QWidget):
    filter_requested = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self._buttons: dict[str, QPushButton] = {}
        for key in ("total", "untranslated", "review", "completed"):
            button = QPushButton()
            button.setFlat(True)
            button.setProperty("summaryKey", key)
            button.clicked.connect(lambda checked=False, value=key: self.filter_requested.emit(value))
            layout.addWidget(button)
            self._buttons[key] = button
        layout.addStretch()
        self.set_summary(StatisticsSummary(0, 0, 0, 0))

    def set_summary(self, summary: StatisticsSummary) -> None:
        values = {
            "total": f"全部 {summary.total}",
            "untranslated": f"未翻译 {summary.untranslated}",
            "review": f"待检查 {summary.needs_review}",
            "completed": f"已完成 {summary.completed}",
        }
        for key, text in values.items():
            self._buttons[key].setText(text)
            self._buttons[key].setEnabled(summary.total > 0)


class WorkflowActionsView(QWidget):
    """Emits stable intent IDs; no business request is constructed here."""

    intent_requested = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._selection_label = QLabel("当前范围：0 条")
        self._selection_label.setAccessibleName("当前操作范围")
        layout.addWidget(self._selection_label)
        self._action_reason = QLabel()
        self._action_reason.setAccessibleName("操作可用性说明")
        self._action_reason.setWordWrap(True)
        layout.addWidget(self._action_reason, 1)
        layout.addStretch()
        self._buttons: dict[IntentId, QPushButton] = {}
        for intent in (IntentId.TRANSLATION_AI, IntentId.TRANSLATION_REVIEW, IntentId.PUBLISH_WRITE):
            button = QPushButton()
            button.setAccessibleName(f"Workbench 操作：{intent.value}")
            button.clicked.connect(lambda checked=False, value=intent: self.intent_requested.emit(value.value))
            layout.addWidget(button)
            self._buttons[intent] = button
        self._more_button = QPushButton("更多 ▾")
        self._more_button.setAccessibleName("更多翻译内容管理操作")
        self._more_button.clicked.connect(self._show_more)
        layout.addWidget(self._more_button)
        self._more_enabled = False

    def set_scope_count(self, count: int) -> None:
        self._selection_label.setText(f"当前范围：{max(0, count)} 条")

    def set_actions(self, actions: tuple[ContextActionViewState, ...]) -> None:
        disabled_reasons: list[str] = []
        for action in actions:
            if action.intent_id is IntentId.WORKBENCH_MANAGE:
                self._more_enabled = action.enabled
                self._more_button.setEnabled(action.enabled)
                self._more_button.setToolTip(action.reason or "危险操作与高级管理")
                self._more_button.setAccessibleDescription(action.reason or "危险操作与高级管理")
                if not action.enabled and action.reason:
                    disabled_reasons.append(action.reason)
                continue
            button = self._buttons[action.intent_id]
            button.setText(action.label)
            button.setEnabled(action.enabled)
            button.setToolTip(action.reason or action.label)
            button.setAccessibleName(action.label)
            button.setAccessibleDescription(action.reason or f"对当前范围执行{action.label}")
            if not action.enabled and action.reason:
                disabled_reasons.append(action.reason)
        self._action_reason.setText("；".join(dict.fromkeys(disabled_reasons)))

    def _show_more(self) -> None:
        if not self._more_enabled:
            return
        menu = QMenu(self)
        action = menu.addAction("管理翻译内容…")
        action.setData(IntentId.WORKBENCH_MANAGE.value)
        action.triggered.connect(lambda: self.intent_requested.emit(IntentId.WORKBENCH_MANAGE.value))
        menu.exec(self._more_button.mapToGlobal(self._more_button.rect().bottomLeft()))


__all__ = ["StatisticsSummaryView", "WorkflowActionsView"]
