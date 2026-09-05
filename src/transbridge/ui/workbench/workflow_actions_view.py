"""Thin Workbench views for summary filters and contextual workflow intents."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QPalette
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QToolButton, QVBoxLayout, QWidget

from transbridge.ui.foundation.components import (
    ComponentKind,
    ComponentStyle,
    ElidedLabel,
    SemanticState,
    reserve_text_width,
)
from transbridge.ui.foundation.tabler_icons import tabler_pixmap
from transbridge.ui.shell.action_catalog import IntentId
from transbridge.ui.workbench.workflow_presenter import (
    ContextActionViewState,
    StatisticsSummary,
)


class _SummaryItem(QPushButton):
    def __init__(self, key: str, icon_id: str, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("tbSummaryItem", True)
        self.setProperty("summaryKey", key)
        self.setMinimumHeight(62)
        row = QHBoxLayout(self)
        row.setContentsMargins(18, 7, 18, 7)
        row.setSpacing(12)
        self._icon_id = icon_id
        self._icon = QLabel(self)
        self._icon.setProperty("tbSummaryIcon", True)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon.setFixedWidth(28)
        self._icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(self._icon)
        copy = QVBoxLayout()
        copy.setSpacing(0)
        self._label = QLabel(self)
        self._label.setProperty("tbSummaryLabel", True)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._value = QLabel(self)
        self._value.setProperty("tbSummaryValue", True)
        self._value.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        copy.addWidget(self._label)
        copy.addWidget(self._value)
        row.addLayout(copy, 1)

    def refresh_icon(self) -> None:
        role = QPalette.ColorRole.LinkVisited if self.property("summaryKey") == "completed" else QPalette.ColorRole.Link
        self._icon.setPixmap(tabler_pixmap(self._icon_id, 28, self.palette().color(role), dpr=self.devicePixelRatioF()))

    def set_content(self, label: str, value: int) -> None:
        self._label.setText(label)
        self._value.setText(str(value))
        self.setAccessibleName(f"{label} {value}")

    def set_description(self, description: str) -> None:
        self.setToolTip(description)
        self.setAccessibleDescription(description)


class StatisticsSummaryView(QWidget):
    filter_requested = pyqtSignal(str)

    def __init__(self, parent=None, *, theme_view=None) -> None:
        super().__init__(parent)
        ComponentStyle.apply_static(self, ComponentKind.CARD)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(0)
        self._buttons: dict[str, _SummaryItem] = {}
        icons = {
            "total": "list-details",
            "untranslated": "circle-dashed",
            "review": "alert-triangle",
            "completed": "circle-check",
        }
        for key in ("total", "untranslated", "review", "completed"):
            button = _SummaryItem(key, icons[key], self)
            button.clicked.connect(lambda checked=False, value=key: self.filter_requested.emit(value))
            layout.addWidget(button, 1)
            self._buttons[key] = button
        self._refresh_icons()
        if theme_view is not None:
            theme_view.subscribe(self, lambda _snapshot: self._refresh_icons())
        self.set_summary(StatisticsSummary(0, 0, 0, 0))

    def _refresh_icons(self) -> None:
        for button in self._buttons.values():
            button.refresh_icon()

    def set_summary(self, summary: StatisticsSummary) -> None:
        for metric in summary.metrics():
            button = self._buttons[metric.key]
            button.set_content(metric.label, metric.value)
            button.set_description(f"{metric.description}，共 {metric.value} 条；点击筛选")
            button.setEnabled(summary.total > 0)


class WorkflowActionsView(QWidget):
    """Emits stable intent IDs; no business request is constructed here."""

    intent_requested = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("tbWorkflowActions")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._title_label = QLabel("词条列表")
        title_font = self._title_label.font()
        title_font.setBold(True)
        self._title_label.setFont(title_font)
        self._title_label.setAccessibleName("词条列表操作区")
        layout.addWidget(self._title_label)
        self._selection_label = QLabel("已选择 0 条")
        self._selection_label.setAccessibleName("当前操作范围")
        layout.addWidget(self._selection_label)
        self._action_reason = ElidedLabel()
        self._action_reason.setAccessibleName("操作可用性说明")
        layout.addWidget(self._action_reason, 1)
        layout.addStretch()
        self._buttons: dict[IntentId, QPushButton | QToolButton] = {}
        self._ai_button = QToolButton(self)
        self._ai_button.setText("AI 翻译")
        self._ai_button.setAccessibleName("AI 翻译")
        self._ai_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        ComponentStyle.apply_static(self._ai_button, ComponentKind.BUTTON)
        ComponentStyle.apply_state(self._ai_button, SemanticState.PRIMARY)
        reserve_text_width(self._ai_button, ("AI 翻译",))
        self._ai_menu = QMenu(self._ai_button)
        self._ai_current_action = QAction("翻译当前内容", self._ai_menu)
        self._ai_batch_action = QAction("批量翻译多个插件", self._ai_menu)
        self._ai_current_action.triggered.connect(lambda: self.intent_requested.emit(IntentId.TRANSLATION_AI.value))
        self._ai_batch_action.triggered.connect(lambda: self.intent_requested.emit(IntentId.TRANSLATION_AI_BATCH.value))
        self._ai_menu.addAction(self._ai_current_action)
        self._ai_menu.addAction(self._ai_batch_action)
        self._ai_button.setMenu(self._ai_menu)
        self._ai_button.clicked.connect(self._request_current_ai)
        layout.addWidget(self._ai_button)
        self._buttons[IntentId.TRANSLATION_AI] = self._ai_button
        for intent in (
            IntentId.TRANSLATION_REVIEW,
            IntentId.PUBLISH_WRITE,
        ):
            button = QPushButton()
            button.setAccessibleName(f"Workbench 操作：{intent.value}")
            ComponentStyle.apply_static(button, ComponentKind.BUTTON)
            reserve_text_width(
                button,
                {
                    IntentId.TRANSLATION_REVIEW: ("检查",),
                    IntentId.PUBLISH_WRITE: ("写回/发布",),
                }[intent],
            )
            button.clicked.connect(lambda checked=False, value=intent: self.intent_requested.emit(value.value))
            layout.addWidget(button)
            self._buttons[intent] = button
        self._more_button = QPushButton("更多 ▾")
        self._more_button.setAccessibleName("更多翻译内容管理操作")
        ComponentStyle.apply_static(self._more_button, ComponentKind.BUTTON)
        self._more_button.clicked.connect(self._show_more)
        layout.addWidget(self._more_button)
        self._more_enabled = False

    def set_scope_count(self, count: int) -> None:
        self._selection_label.setText(f"已选择 {max(0, count)} 条")

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
            if action.intent_id is IntentId.TRANSLATION_AI_BATCH:
                self._ai_batch_action.setEnabled(action.enabled)
                self._ai_batch_action.setToolTip(action.reason or "选择并按顺序翻译多个插件")
                if not action.enabled and action.reason:
                    disabled_reasons.append(action.reason)
                continue
            button = self._buttons[action.intent_id]
            if action.intent_id is IntentId.TRANSLATION_AI:
                self._ai_current_action.setEnabled(action.enabled)
                self._ai_current_action.setToolTip(action.reason or "翻译当前内容")
            button.setText(action.label)
            button.setEnabled(action.enabled)
            button.setToolTip(action.reason or action.label)
            button.setAccessibleName(action.label)
            button.setAccessibleDescription(action.reason or f"对当前范围执行{action.label}")
            if not action.enabled and action.reason:
                disabled_reasons.append(action.reason)
        self._ai_button.setEnabled(self._ai_current_action.isEnabled() or self._ai_batch_action.isEnabled())
        reason = "；".join(dict.fromkeys(disabled_reasons))
        self._action_reason.set_full_text(reason)
        self._action_reason.setToolTip(reason)

    def _request_current_ai(self) -> None:
        if self._ai_current_action.isEnabled():
            self.intent_requested.emit(IntentId.TRANSLATION_AI.value)

    def _show_more(self) -> None:
        if not self._more_enabled:
            return
        menu = QMenu(self)
        action = menu.addAction("管理翻译内容…")
        action.setData(IntentId.WORKBENCH_MANAGE.value)
        action.triggered.connect(lambda: self.intent_requested.emit(IntentId.WORKBENCH_MANAGE.value))
        menu.exec(self._more_button.mapToGlobal(self._more_button.rect().bottomLeft()))


__all__ = ["StatisticsSummaryView", "WorkflowActionsView"]
