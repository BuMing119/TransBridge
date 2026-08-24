from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

from transbridge.smart_assistant.execution_engine import StepResult

from .theme_support import BUTTON_STRUCTURE_STYLE, CARD_STRUCTURE_STYLE, SmartAssistantTheme


class PlanCard(QWidget):
    confirmed = pyqtSignal(list)
    cancelled = pyqtSignal()

    def __init__(self, steps: list, parent=None, *, theme: SmartAssistantTheme | None = None):
        super().__init__(parent)
        self._steps = steps
        self._theme = theme or SmartAssistantTheme()
        self.setObjectName("PlanCard")
        self.setProperty("tbSurface", "card")
        self.setStyleSheet(CARD_STRUCTURE_STYLE)
        total = len(steps)
        self.setAccessibleName(f"执行计划，共 {total} 步")
        self.setAccessibleDescription("计划就绪，等待执行或取消")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)
        self._title = QLabel(f"[Plan] 执行计划（共 {total} 步）")
        layout.addWidget(self._title)
        self._step_list = QListWidget()
        self._step_list.setAccessibleName("计划步骤")
        self._step_list.setMaximumHeight(120)
        self._step_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._step_items: dict[int, QListWidgetItem] = {}
        for step in steps:
            dependencies = step.get("depends_on", [])
            dependency_text = f"  (依赖步骤: {dependencies})" if dependencies else ""
            item = QListWidgetItem(f"步骤 {step['id']}: {step.get('tool', '?')}{dependency_text}")
            self._step_items[step["id"]] = item
            self._step_list.addItem(item)
        layout.addWidget(self._step_list)
        self._progress_label = QLabel(f"就绪 (0/{total})")
        layout.addWidget(self._progress_label)
        row = QHBoxLayout()
        self._exec_btn = QPushButton("执行计划")
        self._cancel_btn = QPushButton("取消")
        for button in (self._exec_btn, self._cancel_btn):
            button.setStyleSheet(BUTTON_STRUCTURE_STYLE)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._exec_btn.setAccessibleName("执行计划")
        self._cancel_btn.setAccessibleName("取消计划")
        self._exec_btn.clicked.connect(self._on_confirm)
        self._cancel_btn.clicked.connect(self._on_cancel)
        row.addStretch()
        row.addWidget(self._exec_btn)
        row.addWidget(self._cancel_btn)
        layout.addLayout(row)
        self.apply_theme(self._theme)

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme
        theme.apply_semantic(self, "info", background=True)
        theme.apply_semantic(self._title, "default")
        theme.apply_semantic(self._step_list, "default")
        theme.apply_semantic(self._progress_label, "muted")
        theme.apply_semantic(self._exec_btn, "success", background=True)
        theme.apply_semantic(self._cancel_btn, "muted", background=True)

    def _on_confirm(self) -> None:
        self._exec_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._exec_btn.setText("执行中...")
        self.setAccessibleDescription("计划执行中")
        self.confirmed.emit(self._steps)

    def _on_cancel(self) -> None:
        self._exec_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self.setAccessibleDescription("计划已取消")
        self.cancelled.emit()

    def on_step_started(self, step_id: int, tool_name: str) -> None:
        item = self._step_items.get(step_id)
        if item is not None:
            item.setText(f"[..] 步骤 {step_id}: {tool_name} - 执行中...")

    def on_step_finished(self, result: StepResult) -> None:
        item = self._step_items.get(result.step_id)
        if item is not None:
            item.setText(
                f"{'[OK]' if result.success else '[FAIL]'} 步骤 {result.step_id}: {result.tool} - {result.message}"
            )

    def on_progress(self, completed: int, total: int) -> None:
        text = f"进行中 ({completed}/{total})"
        self._progress_label.setText(text)
        self._theme.mark_status(self._progress_label, text, "running")

    def on_all_finished(self, results: list) -> None:
        total = len(results)
        ok = sum(1 for result in results if result.success)
        text = f"完成 ({ok}/{total} 成功)"
        self._progress_label.setText(text)
        self._theme.mark_status(self._progress_label, text, "success" if ok == total else "warning")
        self._cancel_btn.setText("关闭")
        self._cancel_btn.setEnabled(True)
        self.setAccessibleDescription(text)


__all__ = ["PlanCard"]
