"""Conversation-local request projections; controls retain authoritative request identities."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QMenu, QProgressBar, QPushButton, QVBoxLayout, QWidget

from .theme_support import BUTTON_STRUCTURE_STYLE, SmartAssistantTheme


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


class RequestProgressCard(QWidget):
    def __init__(self, request, *, control, timeline, undo, theme):
        super().__init__()
        self.request = request
        self._control, self._timeline, self._undo = control, timeline, undo
        self._theme = theme
        self.setAccessibleName("请求进度")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(42, 8, 0, 16)
        layout.setSpacing(8)
        self.goal = QLabel()
        self.goal.setWordWrap(True)
        self.goal.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.goal)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setStyleSheet(
            "QProgressBar { border: none; background-color: palette(alternate-base); }"
            "QProgressBar::chunk { background-color: palette(highlight); }"
        )
        self.progress.setMaximumHeight(5)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        self.expand = QPushButton("执行详情")
        self.expand.setCheckable(True)
        self.expand.setStyleSheet(BUTTON_STRUCTURE_STYLE)
        layout.addWidget(self.expand, alignment=Qt.AlignmentFlag.AlignLeft)
        self.details = QWidget()
        details = QVBoxLayout(self.details)
        details.setContentsMargins(0, 0, 0, 0)
        self.items = QLabel()
        self.items.setWordWrap(True)
        self.items.setTextFormat(Qt.TextFormat.PlainText)
        details.addWidget(self.items)
        self.actions = QPushButton("任务操作与过程…")
        self.actions.setStyleSheet(BUTTON_STRUCTURE_STYLE)
        self.actions.clicked.connect(self._show_actions)
        details.addWidget(self.actions, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.details)
        self.details.hide()
        self.expand.toggled.connect(self.details.setVisible)
        self.resume = QPushButton("继续处理")
        self.resume.setStyleSheet(BUTTON_STRUCTURE_STYLE)
        self.resume.clicked.connect(lambda: self._control(self.request.request_id, "resume"))
        layout.addWidget(self.resume, alignment=Qt.AlignmentFlag.AlignLeft)
        self.undo_message = QLabel()
        self.undo_message.setWordWrap(True)
        self.undo_message.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.undo_message)
        self.undo_button = QPushButton("撤销本轮修改")
        self.undo_button.setStyleSheet(BUTTON_STRUCTURE_STYLE)
        self.undo_button.clicked.connect(self._undo)
        layout.addWidget(self.undo_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.show_undo("", False)
        self.update_request(request)

    def update_request(self, request):
        self.request = request
        self.goal.setText(request.goal)
        done = sum(item.status == "satisfied" for item in request.items)
        self.status.setText(f"{request_status(request)} · {done} / {len(request.items)} 项事项完成")
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
        self.resume.setVisible(bool(request.pause_reasons) and request.status == "open")
        self.apply_theme(self._theme)

    def _show_actions(self):
        menu = QMenu(self)
        menu.addAction("查看执行过程", lambda: self._timeline(self.request.request_id))
        if self.request.status == "open":
            menu.addAction("继续处理", lambda: self._control(self.request.request_id, "resume"))
            menu.addAction("暂停处理", lambda: self._control(self.request.request_id, "pause"))
            menu.addAction("取消任务", lambda: self._control(self.request.request_id, "cancel"))
            menu.addAction("调整事项归属", lambda: self._control(self.request.request_id, "reassign"))
        if request_status(self.request) == "执行结果待核对":
            menu.addAction("核对执行结果", lambda: self._control(self.request.request_id, "reconcile"))
        menu.exec(self.actions.mapToGlobal(self.actions.rect().bottomLeft()))

    def show_undo(self, message, available):
        self.undo_message.setText(message)
        self.undo_message.setVisible(bool(message))
        self.undo_button.setVisible(available)

    def apply_theme(self, theme: SmartAssistantTheme):
        self._theme = theme
        theme.apply_surface(self)
        theme.apply_semantic(self.goal, "default")
        theme.apply_semantic(self.status, "muted")
        theme.apply_semantic(self.items, "default")
        theme.apply_semantic(self.undo_message, "muted")
        for button in (self.expand, self.actions, self.resume, self.undo_button):
            theme.apply_surface(button)


class RequestProgressPresenter:
    """Owns progress-card lifetimes independently of transient transcript bubbles."""

    def __init__(self, binding, message_list, theme):
        self.binding, self.message_list, self.theme = binding, message_list, theme
        self.cards = {}
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
                    undo=self.binding.undo.request,
                    theme=self.theme,
                )
                self.cards[request.request_id] = card
            else:
                card.update_request(request)
            self.message_list.attach(card, tuple(request.source_message_ids))

    def show_undo(self, message, available):
        target = self.binding.undo._target
        for request_id, card in self.cards.items():
            active = target is not None and request_id == target[2]
            card.show_undo(message if active else "", available and active)

    def close(self):
        self.binding.view.requests_changed.disconnect(self.display)
        self.binding.view.undo_changed.disconnect(self.show_undo)
        self.display(())
