"""Read-only, paged view of durable request lifecycle events."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QListWidget, QPlainTextEdit, QPushButton, QVBoxLayout

logger = logging.getLogger(__name__)

_OPERATIONS = {
    "input.accepted": "已接收用户输入",
    "input.routed": "已记录输入归属",
    "input.updated": "输入处理状态已更新",
    "routing.prepared": "正在处理请求归属",
    "routing.applied": "已处理请求归属",
    "routing.rejected": "请求归属未通过校验",
    "routing.failed": "请求归属处理失败，等待重试",
    "routing.cancelled": "已取消处理请求归属",
    "routing.needs_clarification": "请求归属等待澄清",
    "routing.paused": "已暂停处理请求归属",
    "routing.resumed": "已继续处理请求归属",
    "request.created": "已创建请求",
    "request.pause": "已暂停推进",
    "request.resume": "已继续请求",
    "request.interrupt": "已停止本轮生成",
    "request.cancel": "已请求取消",
    "request.wait": "请求进入等待",
    "request.unblock": "已解除请求等待",
    "turn.selected": "已选择下一轮处理请求",
    "turn.failed": "本轮处理失败，已暂停推进",
    "request.reconciled": "用户已核对执行结果",
    "task.pause": "用户已暂停后台任务",
    "task.resume": "用户已恢复后台任务",
    "task.cancel": "用户已发送后台任务取消信号",
    "answer.committed": "回答已保存",
    "task.result": "已记录后台任务结果",
    "recovery.checked": "已核对恢复状态",
    "session.deletion": "已处理会话删除状态",
    "confirmation.changed": "确认状态已更新",
    "request.updated": "请求状态已更新",
}
_ORIGINS = {"user": "用户操作", "model": "模型提案", "runtime": "运行过程", "recovery": "恢复检查"}
_STATES = {
    "open": "待完成",
    "stopping": "取消中",
    "completed": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
    "superseded": "已替换",
    "running": "执行中",
    "pending": "待处理",
    "waiting": "等待处理",
    "satisfied": "已完成",
    "outcome_unknown": "执行结果待核对",
}
_WAITING = {
    "approval": "等待批准",
    "approval_revalidation": "需要重新批准",
    "resource": "等待资源",
    "outcome_unknown": "等待核对执行结果",
    "user_job_control": "等待后台任务处理",
    "confirmation": "等待确认",
    "answer_blocked": "回答事项尚未解决",
    "plan_failed": "执行计划失败",
    "reconciled_result": "结果已核对，等待继续",
    "user_paused": "用户暂停",
    "user_interrupted": "用户停止生成",
    "turn_budget": "自动推进次数已用完",
}


def _state_labels(value):
    """Expose known state vocabulary without leaking arbitrary identifiers or payloads."""
    labels = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "status" and isinstance(item, str) and item in _STATES:
                labels.append(_STATES[item])
            elif key in {"waiting_reasons", "pause_reasons"} and isinstance(item, (list, tuple)):
                labels.extend(_WAITING.get(reason, "等待处理") for reason in item if isinstance(reason, str))
            elif isinstance(item, (dict, list, tuple)):
                labels.extend(_state_labels(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            labels.extend(_state_labels(item))
    return list(dict.fromkeys(labels))


def format_event(event):
    title = _OPERATIONS.get(event.get("operation"), "已记录生命周期变化")
    origin = _ORIGINS.get(event.get("origin"), "状态记录")
    before = "、".join(_state_labels(event.get("before", {})))
    after = "、".join(_state_labels(event.get("after", {})))
    change = f"\n{before or '未记录状态'} → {after or '未记录状态'}" if before or after else ""
    return f"{event.get('timestamp', '')} · {origin}\n{title}{change}"


class RequestTimelineView(QDialog):
    loaded = pyqtSignal(object)

    def __init__(self, load_page, *, request_only=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("请求过程" if request_only else "会话过程")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(640, 520)
        self._load_page = load_page
        self._sequence = 0
        self._events = []
        self._closed = False
        self._busy = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="request-timeline")
        layout = QVBoxLayout(self)
        self.status = QLabel("正在读取已保存的过程……")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.events = QListWidget()
        self.events.setAccessibleName("已保存的生命周期事件")
        self.events.currentRowChanged.connect(self._select)
        layout.addWidget(self.events)
        self.diagnostics = QPushButton("显示所选事件诊断详情")
        self.diagnostics.setCheckable(True)
        layout.addWidget(self.diagnostics)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.hide()
        self.diagnostics.toggled.connect(self.details.setVisible)
        layout.addWidget(self.details)
        actions = QHBoxLayout()
        self.more = QPushButton("加载更多")
        self.more.clicked.connect(self.load_next)
        actions.addWidget(self.more)
        close = QPushButton("关闭")
        close.clicked.connect(self.close)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.loaded.connect(self._receive)
        self.finished.connect(self._shutdown)
        executor = self._executor
        self.destroyed.connect(lambda: executor.shutdown(wait=False, cancel_futures=True))
        self.load_next()

    def load_next(self):
        if self._closed or self._busy:
            return
        self._busy = True
        self.more.setEnabled(False)
        self.status.setText("正在读取已保存的过程……")
        future = self._executor.submit(self._load_page, self._sequence)

        def complete(result):
            try:
                self.loaded.emit(result)
            except RuntimeError:
                logger.debug("Timeline view closed before its read completed")

        future.add_done_callback(complete)

    def _receive(self, future):
        if self._closed:
            return
        self._busy = False
        try:
            page = future.result()
            events = page["events"]
            sequence = page["next_sequence"]
            has_more = page["has_more"]
        except Exception:
            logger.exception("Unable to read the saved request timeline")
            self.status.setText("无法读取已保存的过程，请重试。已保存的请求状态未改变。")
            self.more.setText("重试读取")
            self.more.setEnabled(True)
            return
        self._events.extend(events)
        for event in events:
            self.events.addItem(format_event(event))
        self._sequence = sequence
        self.more.setText("加载更多" if has_more else "刷新后续记录")
        self.more.setEnabled(True)
        self.status.setText(
            f"已显示 {len(self._events)} 条已保存事件（时间为 UTC）。"
            if self._events
            else "没有已保存的过程记录。旧会话可能没有日志；不会从聊天内容补推历史。"
        )
        if self.events.currentRow() < 0 and self._events:
            self.events.setCurrentRow(0)

    def _select(self, row):
        self.details.setPlainText(
            json.dumps(self._events[row], ensure_ascii=False, indent=2) if 0 <= row < len(self._events) else ""
        )

    def _shutdown(self):
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)
