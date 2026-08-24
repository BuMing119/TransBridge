"""Status-bar rendering and context-to-label binding."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QLabel, QMainWindow, QStatusBar

from transbridge.ui.foundation.components import ElidedLabel, SemanticState, StatusBadge, reserve_text_width
from transbridge.ui.presentation import CallbackSubscription, SubscriptionGroup
from transbridge.ui.workers import get_api_status_bus


class ApiStatusIndicator(StatusBadge):
    _SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(self, parent=None) -> None:
        super().__init__(parent=parent)
        self._active = 0
        self._last_ok = True
        self._spin_index = 0
        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._tick)
        reserve_text_width(
            self,
            tuple(f"{spinner} 请求中" for spinner in self._SPINNER) + ("● 正常", "⚠ 异常"),
        )
        self._refresh()

    def on_request_started(self) -> None:
        if self._active == 0:
            self._last_ok = True
        self._active += 1
        if not self._timer.isActive():
            self._timer.start()
        self._refresh()

    def on_request_finished(self, success: bool) -> None:
        self._active = max(0, self._active - 1)
        if not success:
            self._last_ok = False
        if self._active == 0:
            self._timer.stop()
        self._refresh()

    def _tick(self) -> None:
        self._spin_index = (self._spin_index + 1) % len(self._SPINNER)
        self._refresh()

    def _refresh(self) -> None:
        if self._active:
            status_id = "requesting"
            text = f"{self._SPINNER[self._spin_index]} 请求中"
            semantic_state = SemanticState.INFO
        elif self._last_ok:
            status_id = "healthy"
            text = "● 正常"
            semantic_state = SemanticState.SUCCESS
        else:
            status_id = "failed"
            text = "⚠ 异常"
            semantic_state = SemanticState.ERROR
        self.setProperty("tbStatusId", status_id)
        self.set_status(text, semantic_state)


class StatusPresenter:
    def __init__(self, window: QMainWindow, context) -> None:
        self._window = window
        self._context = context
        self._subscriptions = SubscriptionGroup()
        status_bar = QStatusBar(window)
        window.setStatusBar(status_bar)
        self.user_label = ElidedLabel("未登录")
        self.user_label.setFixedWidth(150)
        self.project_label = ElidedLabel("未选择项目")
        self.project_label.setFixedWidth(260)
        self.api_indicator = ApiStatusIndicator(window)
        self.message_label = QLabel("就绪")
        status_bar.addPermanentWidget(self.user_label)
        status_bar.addPermanentWidget(QLabel(" | "))
        status_bar.addPermanentWidget(self.project_label)
        status_bar.addPermanentWidget(QLabel(" | "))
        status_bar.addPermanentWidget(self.api_indicator)
        status_bar.addPermanentWidget(QLabel(" | "))
        status_bar.addWidget(self.message_label)

    def _connect(self, signal, callback: Callable) -> None:
        signal.connect(callback)
        self._subscriptions.add(CallbackSubscription(lambda: self._safe_disconnect(signal, callback)))

    @staticmethod
    def _safe_disconnect(signal, callback: Callable) -> None:
        try:
            signal.disconnect(callback)
        except (RuntimeError, TypeError):
            pass

    def start(self) -> None:
        self._connect(self._context.user_changed, self.render_user)
        self._connect(self._context.project_selected, self.render_project)
        bus = get_api_status_bus()
        self._connect(bus.request_started, self.api_indicator.on_request_started)
        self._connect(bus.request_finished, self.api_indicator.on_request_finished)

    def show_message(self, message: str) -> None:
        self.message_label.setText(message)

    def render_user(self, user) -> None:
        if user:
            name = user.get("nickname") or user.get("username") or "已登录"
            text = f"用户: {name}"
            self.user_label.set_full_text(text)
            self.user_label.setToolTip(text)
        else:
            self.user_label.set_full_text("未登录")
            self.user_label.setToolTip("未登录")

    def render_project(self, project) -> None:
        if project:
            text = f"项目: {project.get('name', '')} (id={project.get('id', '')})"
            self.project_label.set_full_text(text)
            self.project_label.setToolTip(text)
        else:
            self.project_label.set_full_text("未选择项目")
            self.project_label.setToolTip("未选择项目")

    def close(self) -> None:
        self._subscriptions.close()
        self.api_indicator._timer.stop()
