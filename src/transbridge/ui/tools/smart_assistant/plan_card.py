from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget, QListWidgetItem,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QScrollArea  # noqa: F811 — 用于后续扩展

from transbridge.smart_assistant.execution_engine import StepResult


class PlanCard(QWidget):
    """计划确认卡片：蓝色背景，步骤列表 + 依赖标注 + 进度。"""

    confirmed = pyqtSignal(list)
    cancelled = pyqtSignal()

    def __init__(self, steps: list, parent=None):
        super().__init__(parent)
        self._steps = steps
        self.setObjectName("PlanCard")
        self.setStyleSheet(
            "#PlanCard { background-color: #E3F2FD; border: 1px solid #C8D8E8;"
            " border-radius: 10px; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)

        total = len(steps)
        title = QLabel(f"[Plan] 执行计划（共 {total} 步）")
        title.setStyleSheet("font-weight: bold; font-size: 13px; color: #333;")
        layout.addWidget(title)

        # 步骤列表
        self._step_list = QListWidget()
        self._step_list.setMaximumHeight(120)
        self._step_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._step_list.setStyleSheet(
            "QListWidget { border: none; background: transparent; font-size: 12px; }"
        )
        self._step_items: dict[int, QListWidgetItem] = {}  # M59: O(1) step lookup
        for s in steps:
            deps = s.get("depends_on", [])
            dep_str = f"  (依赖步骤: {deps})" if deps else ""
            item_text = f"步骤 {s['id']}: {s.get('tool', '?')}{dep_str}"
            item = QListWidgetItem(item_text)
            self._step_items[s["id"]] = item
            self._step_list.addItem(item)
        layout.addWidget(self._step_list)

        # 进度
        self._progress_label = QLabel(f"就绪 (0/{total})")
        self._progress_label.setStyleSheet("font-size: 11px; color: #888;")
        layout.addWidget(self._progress_label)

        # 按钮
        btn_row = QHBoxLayout()
        self._exec_btn = QPushButton("执行计划")
        self._exec_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #4CAF50; color: white; border: none;"
            "  border-radius: 6px; padding: 4px 16px;"
            "  font-size: 12px; font-weight: bold;"
            "}"
            "QPushButton:hover { background-color: #43A047; }"
        )
        self._exec_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._exec_btn.clicked.connect(self._on_confirm)
        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #f5f5f5; border: 1px solid #ddd;"
            "  border-radius: 6px; padding: 4px 16px; font-size: 12px; color: #666;"
            "}"
            "QPushButton:hover { background-color: #e8e8e8; }"
        )
        self._cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addStretch()
        btn_row.addWidget(self._exec_btn)
        btn_row.addWidget(self._cancel_btn)
        layout.addLayout(btn_row)

    def _on_confirm(self):
        self._exec_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._exec_btn.setText("执行中...")
        self.confirmed.emit(self._steps)

    def _on_cancel(self):
        self._exec_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self.cancelled.emit()

    def on_step_started(self, step_id: int, tool_name: str) -> None:
        item = self._step_items.get(step_id)
        if item is not None:
            item.setText(f"[..] 步骤 {step_id}: {tool_name} - 执行中...")

    def on_step_finished(self, result: StepResult) -> None:
        icon = "[OK]" if result.success else "[FAIL]"
        item = self._step_items.get(result.step_id)
        if item is not None:
            item.setText(
                f"{icon} 步骤 {result.step_id}: {result.tool} - {result.message}"
            )

    def on_progress(self, completed: int, total: int) -> None:
        self._progress_label.setText(f"进行中 ({completed}/{total})")

    def on_all_finished(self, results: list) -> None:
        total = len(results)
        ok = sum(1 for r in results if r.success)
        self._progress_label.setText(f"完成 ({ok}/{total} 成功)")
        self._cancel_btn.setText("关闭")
        self._cancel_btn.setEnabled(True)
