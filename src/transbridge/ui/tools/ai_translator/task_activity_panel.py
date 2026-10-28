"""Compact task activity, bounded history and a visible last warning."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from transbridge.ai_translator.activity import ActivityKind, ActivityMessage

from .task_activity import ActivityBatch, concise_event, event_text
from .task_widget_style import configure_task_button

_KINDS = {
    ActivityKind.STAGE: "进展",
    ActivityKind.RETRY: "重试",
    ActivityKind.WARNING: "提醒",
    ActivityKind.ERROR: "需关注",
}


@dataclass
class _ActivityRow:
    source: str
    event: ActivityMessage
    repeats: int = 1

    @property
    def identity(self):
        return self.source, self.event.batch, self.event.kind, str(self.event)

    def text(self):
        source = f"{self.source} · " if self.source else ""
        repeats = f"（连续 {self.repeats} 次）" if self.repeats > 1 else ""
        prefix = f"{self.event.occurred_at:%H:%M:%S} [{_KINDS[self.event.kind]}] "
        return prefix + source + event_text(self.event) + repeats


class TaskActivityPanel(QWidget):
    """All mutations happen on the GUI thread; one delivery can contain many events."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = deque(maxlen=500)
        self._started = time.monotonic()
        self._last_progress = self._started
        self._signature = None
        self._running = False
        self._clock = time.monotonic
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        header.addWidget(QLabel("任务动态"))
        header.addStretch()
        self.toggle = QPushButton("展开动态记录")
        self.toggle.setToolTip("保留最近 500 条动态，连续重复消息会合并；最终处理结果请使用“查看结果”。")
        self.toggle.setCheckable(True)
        configure_task_button(self.toggle)
        self.toggle.toggled.connect(self._toggle)
        header.addWidget(self.toggle)
        layout.addLayout(header)
        self.timing = QLabel()
        self.timing.setWordWrap(True)
        layout.addWidget(self.timing)
        self.notice = QLabel()
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setWordWrap(True)
        self.notice.hide()
        layout.addWidget(self.notice)
        self.recent = QLabel("阶段变化、自动重试和处理结果会显示在这里。")
        self.recent.setTextFormat(Qt.TextFormat.PlainText)
        self.recent.setWordWrap(True)
        self.recent.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.recent)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.document().setMaximumBlockCount(500)
        self.logs.setPlaceholderText("保留最近 500 条任务动态；重复消息合并显示。")
        self.logs.setMinimumHeight(160)
        self.logs.hide()
        layout.addWidget(self.logs)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)

    def append(self, value):
        events = value.events if isinstance(value, ActivityBatch) else (("", value),)
        scrollbar = self.logs.verticalScrollBar()
        follow = scrollbar.value() >= scrollbar.maximum()
        old_scroll = scrollbar.value()
        for source, value in events:
            event = concise_event(value)
            if event is None:
                continue
            row = _ActivityRow(source, event)
            if self._rows and self._rows[-1].identity == row.identity:
                row.repeats = self._rows[-1].repeats + 1
                self._rows[-1] = row
                cursor = QTextCursor(self.logs.document())
                cursor.movePosition(QTextCursor.MoveOperation.End)
                cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
                cursor.insertText(row.text())
            else:
                self._rows.append(row)
                self.logs.appendPlainText(row.text())
            if event.kind in {ActivityKind.WARNING, ActivityKind.ERROR}:
                self.notice.setText("最近提醒：" + row.text())
                self.notice.show()
        if self._rows:
            self.recent.setText("\n".join(row.text() for row in list(self._rows)[-3:]))
        scrollbar.setValue(scrollbar.maximum() if follow else old_scroll)

    def observe(self, run):
        now = self._clock()
        signature = (run.progress, tuple(run.rows.items()), run.status)
        if signature != self._signature:
            self._last_progress = now
            self._signature = signature
        self._running = run.running and not run.completion_received
        if self._running:
            if not self.timer.isActive():
                self.timer.start()
            self._tick()
        elif self.timer.isActive():
            self._tick()
            self.timer.stop()

    def _tick(self):
        now = self._clock()
        elapsed = max(0, int(now - self._started))
        text = f"任务已用时 {elapsed // 60} 分 {elapsed % 60} 秒"
        if self._running:
            idle = max(0, int(now - self._last_progress))
            text += f" · 距上次进度更新 {idle} 秒"
            if idle >= 30:
                text += "（可结合上方请求状态判断等待情况）"
        self.timing.setText(text)

    def _toggle(self, expanded):
        self.logs.setVisible(expanded)
        self.recent.setVisible(not expanded)
        self.toggle.setText("收起动态记录" if expanded else "展开动态记录")
