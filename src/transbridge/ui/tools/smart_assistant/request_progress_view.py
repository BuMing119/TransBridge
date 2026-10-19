"""Conversation-local request projections; controls retain authoritative request identities."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QProgressBar, QPushButton, QVBoxLayout, QWidget

from .submission_scope import SubmissionScopeView
from .task_presentation import related_tasks, task_progress_text, task_summary
from .theme_support import BUTTON_STRUCTURE_STYLE, PROGRESS_STRUCTURE_STYLE, QUIET_BUTTON_STYLE, SmartAssistantTheme


def request_status(request):
    label = {
        "open": "待完成",
        "stopping": "正在停止",
        "completed": "已完成",
        "failed": "部分失败 / 失败",
        "cancelled": "已取消",
        "superseded": "已替换",
    }[request.status]
    waiting = {
        reason
        for item in request.items
        if item.status in {"pending", "waiting", "running"}
        for reason in item.waiting_reasons
    }
    if request.status == "open":
        if request.pause_reasons:
            label = "已暂停"
        elif waiting & {"approval", "approval_revalidation"}:
            label = "等待确认"
        elif waiting:
            label = "等待处理"
        elif any(item.status == "running" for item in request.items):
            label = "正在执行"
    if any(effect.status == "outcome_unknown" for effect in (*request.effects, *request.dispatches)):
        label = "执行结果待核对"
    return label


class RequestProgressCard(QFrame):
    def __init__(self, request, *, control, timeline, undo, theme, locate=None, review=None):
        super().__init__()
        self.request = request
        self._control, self._timeline, self._undo = control, timeline, undo
        self._theme = theme
        self._tasks = ()
        self.context_wait = None
        self._attention_command = ""
        self._attention_state = "muted"
        self.setAccessibleName("任务进度与结果")
        self.setObjectName("smartAssistantProgress")
        self.setStyleSheet(PROGRESS_STRUCTURE_STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(5)
        summary = QHBoxLayout()
        summary.setSpacing(4)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        summary.addWidget(self.status, 1)
        self.review = QPushButton("查看结果")
        self.review.setToolTip("查看本次修改与执行结果")
        self.review.setEnabled(review is not None)
        self.review.clicked.connect(lambda: review(self.request.request_id, False) if review else None)
        summary.addWidget(self.review)
        self.expand = QPushButton("详情")
        self.expand.setCheckable(True)
        self.expand.setAccessibleName("任务详情")
        self.expand.setAccessibleDescription("展开目标、提交范围、步骤和任务操作")
        self.expand.toggled.connect(self._set_expanded)
        summary.addWidget(self.expand)
        layout.addLayout(summary)
        self.progress = QProgressBar()
        self.progress.setStyleSheet(
            "QProgressBar { border: none; background-color: palette(alternate-base); }"
            "QProgressBar::chunk { background-color: palette(highlight); }"
        )
        self.progress.setFixedHeight(3)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)

        self.attention = QWidget()
        attention = QHBoxLayout(self.attention)
        attention.setContentsMargins(0, 2, 0, 2)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        attention.addWidget(self.notice, 1)
        self.attention_action = QPushButton()
        self.attention_action.clicked.connect(
            lambda: self._control(self.request.request_id, self._attention_command) if self._attention_command else None
        )
        attention.addWidget(self.attention_action)
        self.resume = QPushButton("继续处理")
        self.resume.clicked.connect(lambda: self._control(self.request.request_id, "resume"))
        attention.addWidget(self.resume)
        self.failures = QPushButton("查看失败项")
        self.failures.setEnabled(review is not None)
        self.failures.clicked.connect(lambda: review(self.request.request_id, True) if review else None)
        attention.addWidget(self.failures)
        layout.addWidget(self.attention)

        self.details = QWidget()
        details = QVBoxLayout(self.details)
        details.setContentsMargins(0, 6, 0, 0)
        details.setSpacing(6)
        self.goal = QLabel()
        self.goal.setWordWrap(True)
        self.goal.setTextFormat(Qt.TextFormat.PlainText)
        font = self.goal.font()
        font.setBold(True)
        self.goal.setFont(font)
        details.addWidget(self.goal)
        self.scope_view = SubmissionScopeView(theme)
        details.addWidget(self.scope_view)
        self.items = QLabel()
        self.items.setWordWrap(True)
        self.items.setTextFormat(Qt.TextFormat.PlainText)
        details.addWidget(self.items)
        self.background = QLabel()
        self.background.setWordWrap(True)
        self.background.setTextFormat(Qt.TextFormat.PlainText)
        details.addWidget(self.background)
        primary = QHBoxLayout()
        primary.setSpacing(4)
        self.pause = QPushButton("暂停")
        self.pause.clicked.connect(lambda: self._control(self.request.request_id, "pause"))
        primary.addWidget(self.pause)
        self.cancel = QPushButton("取消任务")
        self.cancel.clicked.connect(lambda: self._control(self.request.request_id, "cancel"))
        primary.addWidget(self.cancel)
        self.locate = QPushButton("原始要求")
        self.locate.setEnabled(locate is not None)
        self.locate.clicked.connect(lambda: locate(self.request.source_message_ids) if locate else None)
        primary.addWidget(self.locate)
        primary.addStretch()
        details.addLayout(primary)
        self.actions = QPushButton("任务操作与过程…")
        self.actions.clicked.connect(self._show_actions)
        details.addWidget(self.actions, alignment=Qt.AlignmentFlag.AlignLeft)
        self.undo_message = QLabel()
        self.undo_message.setWordWrap(True)
        self.undo_message.setTextFormat(Qt.TextFormat.PlainText)
        details.addWidget(self.undo_message)
        self.undo_button = QPushButton("查看撤销范围")
        self.undo_button.clicked.connect(self._undo)
        details.addWidget(self.undo_button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.details)
        self.details.hide()
        self.show_undo("", False)
        self.update_request(request)

    def _set_expanded(self, expanded):
        self.details.setVisible(expanded)
        self.expand.setText("收起" if expanded else "详情")

    def update_request(self, request):
        self.request = request
        self.goal.setText(request.goal)
        done = sum(item.status == "satisfied" for item in request.items)
        self._refresh_summary()
        self.progress.setRange(0, max(1, len(request.items)))
        self.progress.setValue(done)
        self.progress.setVisible(request.status in {"open", "stopping"})
        labels = {
            "pending": "待处理",
            "running": "执行中",
            "waiting": "等待处理",
            "satisfied": "已完成",
            "failed": "失败",
            "cancelled": "未执行",
        }
        self.items.setText(
            "\n".join(
                f"{labels[item.status]} · {item.description}"
                + ("（前置事项失败）" if any(r.startswith("dependency_failed:") for r in item.waiting_reasons) else "")
                for item in request.items
            )
        )
        self.pause.setVisible(not request.pause_reasons and request.status == "open")
        self.cancel.setVisible(request.status == "open")
        self.review.setVisible(request.status not in {"open", "stopping"})
        self._update_attention()
        self.apply_theme(self._theme)

    def set_tasks(self, tasks):
        self._tasks = tuple(tasks)
        self._refresh_summary()

    def set_context_wait(self, value):
        self.context_wait = value
        self._update_attention()

    def _refresh_summary(self):
        related = related_tasks(self.request, self._tasks)
        active = tuple(task for task in related if task.get("status") not in {"completed", "failed", "cancelled"})
        visible = (active or related)[:1] if self.request.status in {"open", "stopping"} else ()
        text = task_summary(self.request, visible)
        if visible and len(related) > 1:
            text += f" · 另有 {len(related) - 1} 项后台工作"
        if self.request.status == "completed" and request_status(self.request) == "已完成":
            text = text.removeprefix("已完成 · ")
        self.status.setText(text)
        self.status.setToolTip(self.request.goal)
        background = task_progress_text(related)
        self.background.setText(background)
        self.background.setVisible(bool(background))

    def _update_attention(self):
        request = self.request
        waiting = {
            reason
            for item in request.items
            if item.status in {"pending", "waiting", "running"}
            for reason in item.waiting_reasons
        }
        failed = request.status == "failed" or any(i.status == "failed" for i in request.items)
        unknown = request_status(request) == "执行结果待核对"
        paused = bool(request.pause_reasons) and request.status == "open"
        approval = bool(waiting & {"approval", "approval_revalidation"}) and request.status == "open"
        self._attention_command = ""
        self._attention_state = "warning"
        self.attention_action.hide()
        self.resume.setText("查看待确认操作" if approval else "继续处理")
        self.resume.setVisible((paused or approval) and not unknown)
        self.failures.setVisible(failed)
        if unknown:
            text = "部分操作的执行结果尚未核实，请先核对。"
            self.attention_action.setText("核对执行结果")
            self._attention_command = "reconcile"
        elif self.context_wait and request.status == "open":
            text = "上下文准备暂停：" + self.context_wait.get("message", "请重试上下文准备。")
            if paused:
                text += " 重试上下文不会解除任务暂停。"
            self.attention_action.setText("重试上下文")
            self._attention_command = "retry_context"
        elif approval:
            text = "任务已暂停，仍有操作等待你确认。" if paused else "有操作等待你确认后才能继续。"
            self.resume.hide()
            self.attention_action.setText("查看待确认操作")
            self._attention_command = "resume"
        elif paused:
            text = "任务已暂停，可继续处理。"
        elif failed:
            text = "有事项未能完成，请查看失败原因。"
            self._attention_state = "error"
            if request.status == "failed" and not request.unsettled:
                self.attention_action.setText("重新处理")
                self._attention_command = "restart"
        elif request.status == "cancelled" and not request.unsettled:
            text = "任务已取消，已完成的结果仍保留。"
            self.attention_action.setText("重新处理")
            self._attention_command = "restart"
        else:
            text = ""
        self.notice.setText(text)
        self.attention_action.setVisible(bool(self._attention_command))
        self.attention.setVisible(bool(text))

    def _show_actions(self):
        menu = QMenu(self)
        menu.addAction("查看执行过程", lambda: self._timeline(self.request.request_id))
        if self.request.status == "open":
            menu.addAction("继续处理", lambda: self._control(self.request.request_id, "resume"))
            menu.addAction("暂停处理", lambda: self._control(self.request.request_id, "pause"))
            menu.addAction("取消任务", lambda: self._control(self.request.request_id, "cancel"))
            menu.addAction("把未执行事项移到其他任务", lambda: self._control(self.request.request_id, "reassign"))
        if request_status(self.request) == "执行结果待核对":
            menu.addAction("核对执行结果", lambda: self._control(self.request.request_id, "reconcile"))
        elif self.request.status in {"failed", "cancelled"} and not self.request.unsettled:
            menu.addAction("重新处理全部要求…", lambda: self._control(self.request.request_id, "restart"))
        menu.exec(self.actions.mapToGlobal(self.actions.rect().bottomLeft()))

    def show_undo(self, message, available):
        self.undo_message.setText(message)
        self.undo_message.setVisible(bool(message))
        self.undo_button.setVisible(available)

    def apply_theme(self, theme: SmartAssistantTheme):
        self._theme = theme
        theme.apply_surface(self, alternate=True)
        theme.apply_semantic(self.goal, "default")
        theme.apply_semantic(self.status, "muted")
        theme.apply_semantic(self.items, "default")
        theme.apply_semantic(self.background, "muted")
        theme.apply_semantic(self.notice, self._attention_state)
        theme.apply_semantic(self.undo_message, "muted")
        self.scope_view.apply_theme(theme)
        for button in (
            self.expand,
            self.actions,
            self.resume,
            self.pause,
            self.cancel,
            self.locate,
            self.review,
            self.failures,
            self.undo_button,
            self.scope_view.details_button,
            self.attention_action,
        ):
            button.setStyleSheet(QUIET_BUTTON_STYLE)
            theme.apply_surface(button)
        for button in (self.attention_action, self.resume, self.failures):
            button.setStyleSheet(BUTTON_STRUCTURE_STYLE)
            theme.apply_accent(button)


class RequestProgressPresenter:
    """Owns progress-card lifetimes independently of transient transcript bubbles."""

    def __init__(self, binding, message_list, theme):
        self.binding, self.message_list, self.theme = binding, message_list, theme
        self.cards = {}
        self._tasks = ()
        binding.view.requests_changed.connect(self.display)
        binding.view.undo_changed.connect(self.show_undo)

    def display(self, requests):
        current = {r.request_id for r in requests}
        for key in set(self.cards) - current:
            card = self.cards.pop(key)
            self.message_list.detach(card)
            card.deleteLater()
        for request in requests:
            card = self.cards.get(request.request_id)
            if card is None:
                card = RequestProgressCard(
                    request,
                    control=self.binding.control,
                    timeline=self.binding.management.show_timeline,
                    undo=lambda _checked=False, rid=request.request_id: self.review_result(rid),
                    theme=self.theme,
                    locate=self.locate_source,
                    review=self.review_result,
                )
                self.cards[request.request_id] = card
            else:
                card.update_request(request)
            card.scope_view.set_scope(self.binding.view.scopes.get(request.request_id))
            card.set_context_wait(getattr(self.binding.view, "context_waits", {}).get(request.request_id))
            card.set_tasks(self._tasks)
            self.message_list.attach(card, tuple(request.source_message_ids))

    def set_tasks(self, tasks):
        self._tasks = tuple(tasks)
        for card in self.cards.values():
            card.set_tasks(tasks)

    def locate_source(self, message_ids):
        for message_id in message_ids:
            if self.message_list.jump_to_message(message_id):
                return
        self.binding.facade.add_system_message("这项任务的原始消息未保存在当前会话中。")

    def review_result(self, request_id, failures_only=False):
        self.binding.result_review.show(request_id, failures_only=failures_only)

    def show_undo(self, message, available):
        target = self.binding.undo._target
        for request_id, card in self.cards.items():
            active = target is not None and request_id == target[2]
            card.show_undo(message if active else "", available and active)

    def close(self):
        self.binding.view.requests_changed.disconnect(self.display)
        self.binding.view.undo_changed.disconnect(self.show_undo)
        self.display(())
