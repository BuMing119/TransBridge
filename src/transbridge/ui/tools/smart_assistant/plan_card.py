from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget, QListWidgetItem,
)
from PyQt6.QtCore import pyqtSignal

from .execution_engine import StepResult


class PlanCard(QWidget):
    """计划确认卡片：蓝色背景，步骤列表 + 依赖标注 + 进度。"""

    confirmed = pyqtSignal(list)
    cancelled = pyqtSignal()

    def __init__(self, steps: list, parent=None):
        super().__init__(parent)
        self._steps = steps
        self.setObjectName("PlanCard")
        self.setStyleSheet(
            "#PlanCard { background-color: #E3F2FD; border-radius: 8px; padding: 8px; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        total = len(steps)
        title = QLabel(f"📋 执行计划（共 {total} 步）")
        title.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(title)

        # 步骤列表
        self._step_list = QListWidget()
        for s in steps:
            deps = s.get("depends_on", [])
            dep_str = f"  (依赖步骤: {deps})" if deps else ""
            item_text = f"步骤 {s['id']}: {s.get('tool', '?')}{dep_str}"
            item = QListWidgetItem(item_text)
            self._step_list.addItem(item)
        layout.addWidget(self._step_list)

        # 进度
        self._progress_label = QLabel(f"就绪 (0/{total})")
        self._progress_label.setStyleSheet("font-size: 11px; color: #666;")
        layout.addWidget(self._progress_label)

        # 按钮
        btn_row = QHBoxLayout()
        self._exec_btn = QPushButton("执行计划")
        self._exec_btn.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold;"
        )
        self._exec_btn.clicked.connect(self._on_confirm)
        self._cancel_btn = QPushButton("取消")
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
        for i in range(self._step_list.count()):
            item = self._step_list.item(i)
            s = self._steps[i]
            if s["id"] == step_id:
                item.setText(f"⏳ 步骤 {step_id}: {tool_name} - 执行中...")
                break

    def on_step_finished(self, result: StepResult) -> None:
        icon = "✅" if result.success else "❌"
        for i in range(self._step_list.count()):
            item = self._step_list.item(i)
            s = self._steps[i]
            if s["id"] == result.step_id:
                item.setText(
                    f"{icon} 步骤 {result.step_id}: {result.tool} - {result.message}"
                )
                break

    def on_progress(self, completed: int, total: int) -> None:
        self._progress_label.setText(f"进行中 ({completed}/{total})")

    def on_all_finished(self, results: list) -> None:
        total = len(results)
        ok = sum(1 for r in results if r.success)
        self._progress_label.setText(f"完成 ({ok}/{total} 成功)")
        self._cancel_btn.setText("关闭")
        self._cancel_btn.setEnabled(True)
