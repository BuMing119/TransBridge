from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

from transbridge.smart_assistant.execution_engine import StepResult

from .operation_preview import OperationPreview, operation_name, operation_summary
from .theme_support import BUTTON_STRUCTURE_STYLE, CARD_STRUCTURE_STYLE, SmartAssistantTheme


class PlanCard(QWidget):
    confirmed = pyqtSignal(list)
    cancelled = pyqtSignal()

    def __init__(self, steps: list, parent=None, *, theme: SmartAssistantTheme | None = None):
        super().__init__(parent)
        self._steps = steps
        self._pending = True
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
            item = QListWidgetItem(
                f"步骤 {step['id']}: {operation_name(step)}{dependency_text}\n{operation_summary(step)}"
            )
            item.setToolTip(item.text())
            self._step_items[step["id"]] = item
            self._step_list.addItem(item)
        layout.addWidget(self._step_list)
        self._preview = OperationPreview(steps)
        layout.addWidget(self._preview)
        self._progress_label = QLabel(f"就绪 (0/{total})")
        self._progress_label.setTextFormat(Qt.TextFormat.PlainText)
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

    def set_change_previews(self, previews):
        self._preview.set_previews(previews)
        for step, preview in zip(self._steps, previews, strict=True):
            item = self._step_items[step["id"]]
            item.setText(f"{operation_name(step)}\n{operation_summary(step, preview)}")
            item.setToolTip(item.text())

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme
        theme.apply_semantic(self, "info", background=True)
        theme.apply_semantic(self._title, "default")
        theme.apply_semantic(self._step_list, "default")
        theme.apply_semantic(self._progress_label, "muted")
        theme.apply_semantic(self._exec_btn, "success", background=True)
        theme.apply_semantic(self._cancel_btn, "muted", background=True)

    def expire(self) -> None:
        self._pending = False
        self._exec_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._exec_btn.setText("已失效")
        self._progress_label.setText("确认已失效")
        self.setAccessibleDescription("计划确认已失效，请使用当前请求的确认操作")

    def _on_confirm(self) -> None:
        if not self._pending:
            return
        self._pending = False
        self._exec_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._exec_btn.setText("执行中...")
        self.setAccessibleDescription("计划执行中")
        self.confirmed.emit(self._steps)

    def _on_cancel(self) -> None:
        if not self._pending:
            return
        self._pending = False
        self._exec_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self.setAccessibleDescription("计划已取消")
        self._exec_btn.setText("已取消")
        self._progress_label.setText("计划已取消，尚未执行")
        self.cancelled.emit()

    def on_step_started(self, step_id: int, tool_name: str) -> None:
        item = self._step_items.get(step_id)
        if item is not None:
            item.setText(f"[..] 步骤 {step_id}: {operation_name({'tool': tool_name})} - 执行中...")

    def on_step_finished(self, result: StepResult) -> None:
        item = self._step_items.get(result.step_id)
        if item is not None:
            cancelled = isinstance(result.data, dict) and result.data.get("status") == "cancelled"
            icon = "[CANCELLED]" if cancelled else "[OK]" if result.success else "[FAIL]"
            item.setText(f"{icon} 步骤 {result.step_id}: {operation_name({'tool': result.tool})} - {result.message}")
            item.setToolTip(item.text())

    def on_progress(self, completed: int, total: int) -> None:
        text = f"进行中 ({completed}/{total})"
        self._progress_label.setText(text)
        self._theme.mark_status(self._progress_label, text, "running")

    def on_all_finished(self, results: list) -> None:
        total = len(self._steps)
        ok = sum(1 for result in results if result.success)
        cancelled = any(
            isinstance(result.data, dict) and result.data.get("status") == "cancelled" for result in results
        )
        for result in results:
            self.on_step_finished(result)
        if cancelled:
            text = f"已取消 ({ok}/{total} 步成功)"
        elif len(results) < total:
            text = f"结果不完整 ({len(results)}/{total} 步已返回)"
        else:
            text = f"完成 ({ok}/{total} 成功)"
        self._progress_label.setText(text)
        self._theme.mark_status(self._progress_label, text, "success" if ok == total else "warning")
        self._exec_btn.setText("已取消" if cancelled else "已完成" if ok == total else "请查看结果")
        self._exec_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self.setAccessibleDescription(text)

    def set_failure(self, message: str) -> None:
        self._exec_btn.setText("执行失败")
        self._progress_label.setText(message)
        self._progress_label.setWordWrap(True)
        self._theme.mark_status(self._progress_label, message, "error")
        self.setAccessibleDescription(message)

    def interrupt(self) -> None:
        self._exec_btn.setText("结果待核对")
        self._progress_label.setText("已停止跟进此计划；未收到最终结果，请核对任务记录。")
        self._progress_label.setWordWrap(True)
        self.setAccessibleDescription(self._progress_label.text())


__all__ = ["PlanCard"]
