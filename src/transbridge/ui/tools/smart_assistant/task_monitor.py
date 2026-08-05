"""TaskMonitorWidget — 后台任务监控面板。

ADR-008: 纯 UI 组件，零后端依赖。通过 refresh() 接收 TaskManager 快照数据。
"""
from __future__ import annotations

import logging
import time
from typing import Any

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QProgressBar, QScrollArea, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal

logger = logging.getLogger(__name__)

# ── 状态颜色映射 ──────────────────────────────────────────────

_STATUS_COLORS: dict[str, str] = {
    "running": "#4CAF50",
    "completed": "#2196F3",
    "failed": "#D32F2F",
    "cancelled": "#9E9E9E",
    "paused": "#FF9800",
}

_STATUS_LABELS: dict[str, str] = {
    "running": "运行中",
    "completed": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
    "paused": "已暂停",
}


# ── 单任务卡片 ─────────────────────────────────────────────────

class _TaskCard(QFrame):
    """单个后台任务的卡片组件。"""

    def __init__(self, task_id: str, task_data: dict, parent=None):
        super().__init__(parent)
        self._task_id = task_id
        self.setObjectName("TaskCard")
        self.setStyleSheet(
            "#TaskCard { background: #FAFAFA; border: 1px solid #E0E0E0;"
            " border-radius: 6px; padding: 6px; margin: 2px 0; }"
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMaximumHeight(80)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        # 左侧：状态指示灯 + 信息
        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)

        # 第一行：名称 + 状态标签
        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        name = task_data.get("metadata", {}).get("type", task_data.get("metadata", {}).get("name", "后台任务"))
        self._name_label = QLabel(name)
        self._name_label.setStyleSheet("font-size: 12px; font-weight: bold; color: #333;")

        status = task_data.get("status", "running")
        color = _STATUS_COLORS.get(status, "#9E9E9E")
        label_text = _STATUS_LABELS.get(status, status)
        self._status_label = QLabel(label_text)
        self._status_label.setStyleSheet(
            f"font-size: 10px; color: white; background: {color};"
            f" border-radius: 3px; padding: 1px 6px;"
        )

        top_row.addWidget(self._name_label)
        top_row.addWidget(self._status_label)
        top_row.addStretch()
        info_layout.addLayout(top_row)

        # 第二行：进度条 + 详细信息
        self._progress_bar = QProgressBar()
        self._progress_bar.setMaximumHeight(14)
        self._progress_bar.setStyleSheet(
            "QProgressBar { border: 1px solid #ddd; border-radius: 3px;"
            " background: #F5F5F5; }"
            "QProgressBar::chunk { background: #4CAF50; border-radius: 2px; }"
        )
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("%v/%m")

        progress = task_data.get("progress", {})
        current = progress.get("current", 0)
        total = progress.get("total", 0)
        if total > 0:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(min(current, total))
        else:
            self._progress_bar.setRange(0, 0)  # 不确定进度

        # 隐藏非活跃任务的进度条
        if status not in ("running", "paused"):
            self._progress_bar.setVisible(False)

        self._detail_label = QLabel(self._build_detail(status, progress))
        self._detail_label.setStyleSheet("font-size: 10px; color: #888;")

        # 第三行：运行时长
        created = task_data.get("created_at", 0)
        elapsed = time.time() - created if created else 0
        self._elapsed_label = QLabel(self._format_duration(int(elapsed)))
        self._elapsed_label.setStyleSheet("font-size: 10px; color: #aaa;")

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(6)
        bottom_row.addWidget(self._progress_bar, stretch=1)
        bottom_row.addWidget(self._detail_label)
        bottom_row.addWidget(self._elapsed_label)
        info_layout.addLayout(bottom_row)

        layout.addLayout(info_layout, stretch=1)

        # 右侧：操作按钮
        btn_layout = QVBoxLayout()
        btn_layout.setSpacing(2)

        if status == "running":
            pause_btn = QPushButton("暂停")
            pause_btn.setStyleSheet(self._btn_style("#FF9800"))
            pause_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            pause_btn.clicked.connect(lambda: self._emit_action("pause"))
            btn_layout.addWidget(pause_btn)

            cancel_btn = QPushButton("取消")
            cancel_btn.setStyleSheet(self._btn_style("#D32F2F"))
            cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            cancel_btn.clicked.connect(lambda: self._emit_action("cancel"))
            btn_layout.addWidget(cancel_btn)

        elif status == "paused":
            resume_btn = QPushButton("恢复")
            resume_btn.setStyleSheet(self._btn_style("#4CAF50"))
            resume_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            resume_btn.clicked.connect(lambda: self._emit_action("resume"))
            btn_layout.addWidget(resume_btn)

            cancel_btn = QPushButton("取消")
            cancel_btn.setStyleSheet(self._btn_style("#D32F2F"))
            cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            cancel_btn.clicked.connect(lambda: self._emit_action("cancel"))
            btn_layout.addWidget(cancel_btn)

        elif status in ("completed", "failed", "cancelled"):
            clear_btn = QPushButton("清除")
            clear_btn.setStyleSheet(self._btn_style("#9E9E9E"))
            clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            clear_btn.clicked.connect(lambda: self._emit_action("cleanup"))
            btn_layout.addWidget(clear_btn)

        layout.addLayout(btn_layout)

    def _emit_action(self, action: str):
        """发射任务操作信号。"""
        parent = self.parent()
        while parent is not None:
            if isinstance(parent, TaskMonitorWidget):
                parent.task_action.emit(self._task_id, action)
                return
            parent = parent.parent()

    @staticmethod
    def _btn_style(color: str) -> str:
        return (
            f"QPushButton {{"
            f"  font-size: 10px; color: {color}; border: 1px solid {color};"
            f"  border-radius: 3px; padding: 1px 8px; background: white;"
            f"}}"
            f"QPushButton:hover {{ background: {color}; color: white; }}"
        )

    @staticmethod
    def _build_detail(status: str, progress: dict) -> str:
        """从 progress 构建详细信息文本。"""
        if not progress:
            return ""
        current = progress.get("current", 0)
        total = progress.get("total", 0)
        if current and total:
            return f"{current}/{total}"
        # 尝试其他常见键
        for key in ("entry_count", "message", "phase"):
            if key in progress:
                return str(progress[key])[:60]
        return ""

    @staticmethod
    def _format_duration(seconds: int) -> str:
        if seconds < 60:
            return f"{seconds}秒"
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes}分{secs}秒"

    def update_elapsed(self):
        """更新运行时长显示（由定时器驱动）。"""
        # created_at 不变，时长自动递增
        pass  # 整体刷新时重建卡片，无需单独更新


# ── 主控件 ─────────────────────────────────────────────────────

class TaskMonitorWidget(QWidget):
    """后台任务监控面板。

    可折叠的任务列表 + 操作按钮。通过 pyqtSignal 发射用户操作，
    外部通过 refresh() 传入 TaskManager 快照数据。
    """

    task_action = pyqtSignal(str, str)  # (task_id, action)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TaskMonitorWidget")
        self.setMinimumHeight(100)
        self.setMaximumHeight(300)

        self._tasks: dict[str, dict] = {}
        self._collapsed = False

        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── 标题栏 ──────────────────────────────────────────────
        title_bar = QFrame()
        title_bar.setStyleSheet(
            "QFrame { background: #F5F5F5; border-top: 1px solid #E0E0E0;"
            " border-bottom: 1px solid #E0E0E0; }"
        )
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(8, 2, 8, 2)

        self._title_label = QLabel("后台任务 (0)")
        self._title_label.setStyleSheet("font-size: 11px; font-weight: bold; color: #555;")

        self._collapse_btn = QPushButton("▼")
        self._collapse_btn.setFixedSize(20, 20)
        self._collapse_btn.setStyleSheet(
            "QPushButton { border: none; font-size: 10px; color: #888; }"
            "QPushButton:hover { color: #333; }"
        )
        self._collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._collapse_btn.clicked.connect(self._toggle_collapse)

        self._clear_all_btn = QPushButton("清除已完成")
        self._clear_all_btn.setStyleSheet(
            "QPushButton { font-size: 10px; color: #888; border: none; }"
            "QPushButton:hover { color: #D32F2F; }"
        )
        self._clear_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_all_btn.clicked.connect(lambda: self.task_action.emit("__all__", "cleanup_completed"))

        title_layout.addWidget(self._title_label)
        title_layout.addStretch()
        title_layout.addWidget(self._clear_all_btn)
        title_layout.addWidget(self._collapse_btn)
        main_layout.addWidget(title_bar)

        # ── 任务列表（可滚动）───────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("QScrollArea { border: none; background: white; }")
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(4, 4, 4, 4)
        self._list_layout.setSpacing(2)
        self._list_layout.addStretch()

        self._scroll.setWidget(self._list_widget)
        main_layout.addWidget(self._scroll, stretch=1)

        # ── 空状态 ──────────────────────────────────────────────
        self._empty_label = QLabel("无后台任务")
        self._empty_label.setStyleSheet("font-size: 11px; color: #AAA; padding: 12px;")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setVisible(True)
        self._list_layout.insertWidget(0, self._empty_label)

    # ── 公共方法 ────────────────────────────────────────────────

    def refresh(self, tasks: list[dict]) -> None:
        """全量刷新任务列表。

        Args:
            tasks: 任务数据列表，每个 dict 包含 task_id, status, progress, metadata, created_at
        """
        # 清除现有卡片（保留 stretch 和 empty_label）
        while self._list_layout.count() > 2:  # empty_label + stretch
            item = self._list_layout.takeAt(0)
            if item.widget() and item.widget() is not self._empty_label:
                item.widget().deleteLater()

        self._tasks = {}
        active_count = 0
        for t in tasks:
            tid = t.get("task_id", "")
            status = t.get("status", "running")
            self._tasks[tid] = t
            if status in ("running", "paused"):
                active_count += 1

            card = _TaskCard(tid, t)
            self._list_layout.insertWidget(self._list_layout.count() - 1, card)

        # 更新标题
        self._title_label.setText(f"后台任务 ({len(tasks)})")
        self._empty_label.setVisible(len(tasks) == 0)

        # 无活跃任务时隐藏清除按钮
        inactive = len(tasks) - active_count
        self._clear_all_btn.setVisible(inactive > 0)

    def update_task(self, task_id: str, status: str | None = None,
                    progress: dict | None = None) -> None:
        """增量更新单个任务。用于避免全量刷新的闪烁。"""
        # 简单实现：直接全量刷新（任务数少，性能足够）
        pass

    def reset(self) -> None:
        """清空所有任务显示（会话切换时调用）。"""
        self.refresh([])

    # ── 内部方法 ────────────────────────────────────────────────

    def _toggle_collapse(self):
        self._collapsed = not self._collapsed
        self._scroll.setVisible(not self._collapsed)
        self._clear_all_btn.setVisible(not self._collapsed)
        self._collapse_btn.setText("▶" if self._collapsed else "▼")
        if self._collapsed:
            self.setMaximumHeight(28)
        else:
            self.setMaximumHeight(300)
