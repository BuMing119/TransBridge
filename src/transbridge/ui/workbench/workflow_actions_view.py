# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Thin Workbench views for summary filters and contextual workflow intents."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QToolButton, QWidget

from transbridge.ui.foundation.components import (
    ComponentKind,
    ComponentStyle,
    ElidedLabel,
    SemanticState,
    reserve_text_width,
)
from transbridge.ui.shell.action_catalog import IntentId
from transbridge.ui.workbench.filter_flow_layout import FilterFlowLayout
from transbridge.ui.workbench.workflow_presenter import (
    ContextActionViewState,
    StatisticsSummary,
)


class StatisticsSummaryView(QWidget):
    filter_requested = pyqtSignal(str)

    def __init__(self, parent=None, *, theme_view=None) -> None:
        super().__init__(parent)
        self.setAccessibleName("词条状态筛选")
        layout = FilterFlowLayout(self)
        layout.setSpacing(8)
        self._buttons: dict[str, QPushButton] = {}
        summary = StatisticsSummary(0, 0, 0, 0)
        for metric in summary.metrics():
            key = metric.key
            button = QPushButton(self)
            button.setCheckable(True)
            button.setProperty("tbSummaryItem", True)
            button.setProperty("summaryKey", key)
            button.clicked.connect(lambda checked=False, value=key: self.filter_requested.emit(value))
            layout.addWidget(button)
            self._buttons[key] = button
        self.set_summary(summary)
        self.set_stage_filter(frozenset())

    def set_summary(self, summary: StatisticsSummary) -> None:
        for metric in summary.metrics():
            button = self._buttons[metric.key]
            button.setText(f"{metric.label} {metric.value}")
            description = f"{metric.description}，共 {metric.value} 条；点击筛选"
            button.setToolTip(description)
            button.setAccessibleDescription(description)
            button.setEnabled(summary.total > 0)

    def set_stage_filter(self, stages: frozenset[int]) -> None:
        active = StatisticsSummary.active_filter(stages)
        for key, button in self._buttons.items():
            button.setChecked(key == active)


class WorkflowActionsView(QWidget):
    """Emits stable intent IDs; no business request is constructed here."""

    intent_requested = pyqtSignal(str)

    def __init__(self, parent=None, *, separate_status: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("tbWorkflowActions")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.status_view = QWidget(self)
        self.status_view.setAccessibleName("词条列表状态")
        status_layout = QHBoxLayout(self.status_view)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(8)
        self._selection_label = QLabel("已选择 0 条")
        self._selection_label.setAccessibleName("已选词条与筛选结果数量")
        status_layout.addWidget(self._selection_label)
        self._action_reason = ElidedLabel()
        self._action_reason.setAccessibleName("操作可用性说明")
        status_layout.addWidget(self._action_reason, 1)
        if not separate_status:
            layout.addWidget(self.status_view, 1)
        self._buttons: dict[IntentId, QPushButton] = {}
        self._ai_button = QPushButton(self)
        self._ai_button.setText("AI 翻译")
        self._ai_button.setAccessibleName("AI 翻译")
        ComponentStyle.apply_static(self._ai_button, ComponentKind.BUTTON)
        ComponentStyle.apply_state(self._ai_button, SemanticState.PRIMARY)
        reserve_text_width(self._ai_button, ("AI 翻译",))
        self._ai_button.clicked.connect(lambda: self.intent_requested.emit(IntentId.TRANSLATION_AI.value))
        layout.addWidget(self._ai_button)
        self._buttons[IntentId.TRANSLATION_AI] = self._ai_button
        export_button = QPushButton("导出文件")
        ComponentStyle.apply_static(export_button, ComponentKind.BUTTON)
        export_button.clicked.connect(lambda: self.intent_requested.emit(IntentId.PUBLISH_WRITE.value))
        layout.addWidget(export_button)
        self._buttons[IntentId.PUBLISH_WRITE] = export_button

        self._more_button = QToolButton()
        self._more_button.setText("更多")
        self._more_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._more_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._more_button.setAccessibleName("更多翻译内容管理操作")
        ComponentStyle.apply_static(self._more_button, ComponentKind.BUTTON)
        layout.addWidget(self._more_button)
        self._more_button.clicked.connect(self._request_management)

    def set_management_menu(self, menu: QMenu) -> None:
        self._more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._more_button.setMenu(menu)
        self._more_button.setEnabled(True)

    def open_management_menu(self) -> None:
        self._more_button.showMenu()

    def set_scope_count(self, count: int, *, filtered_count: int | None = None) -> None:
        text = f"已选 {max(0, count)} 条"
        if filtered_count is not None:
            text += f" / 筛选结果 {max(0, filtered_count)} 条"
        self._selection_label.setText(text)

    def set_actions(self, actions: tuple[ContextActionViewState, ...]) -> None:
        disabled_reasons: list[str] = []
        for action in actions:
            if action.intent_id is IntentId.WORKBENCH_MANAGE:
                if self._more_button.menu() is None:
                    self._more_button.setEnabled(action.enabled)
                description = "打开、导入和管理翻译内容"
                self._more_button.setToolTip(description)
                self._more_button.setAccessibleDescription(description)
                continue
            button = self._buttons.get(action.intent_id)
            if button is None:
                continue
            button.setText(action.label)
            button.setEnabled(action.enabled)
            description = action.reason or (
                "导出当前文件的全部词条，不受表格选择或筛选限制；打开独立窗口选择格式和保存位置。"
                if action.intent_id is IntentId.PUBLISH_WRITE
                else action.label
            )
            button.setToolTip(description)
            button.setAccessibleName(action.label)
            button.setAccessibleDescription(description)
            if not action.enabled and action.reason:
                disabled_reasons.append(action.reason)
        reason = "；".join(dict.fromkeys(disabled_reasons))
        self._action_reason.set_full_text(reason)
        self._action_reason.setToolTip(reason)

    def _request_management(self) -> None:
        if self._more_button.menu() is None:
            self.intent_requested.emit(IntentId.WORKBENCH_MANAGE.value)


__all__ = ["StatisticsSummaryView", "WorkflowActionsView"]
