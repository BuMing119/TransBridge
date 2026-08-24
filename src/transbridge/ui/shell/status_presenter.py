"""Status-bar rendering and context-to-label binding."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QLabel, QMainWindow, QStatusBar

from transbridge.ui.presentation import CallbackSubscription, SubscriptionGroup
from transbridge.ui.workers import get_api_status_bus


class ApiStatusIndicator(QLabel):
    _SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._active = 0
        self._last_ok = True
        self._spin_index = 0
        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._tick)
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
            self.setText(f'<span style="color:#888">{self._SPINNER[self._spin_index]} 请求中</span>')
        elif self._last_ok:
            self.setText('<span style="color:green">● 正常</span>')
        else:
            self.setText('<span style="color:red">● 异常</span>')


class StatusPresenter:
    def __init__(self, window: QMainWindow, context) -> None:
        self._window = window
        self._context = context
        self._subscriptions = SubscriptionGroup()
        status_bar = QStatusBar(window)
        window.setStatusBar(status_bar)
        self.user_label = QLabel("未登录")
        self.project_label = QLabel("未选择项目")
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
            self.user_label.setText(f"用户: {name}")
        else:
            self.user_label.setText("未登录")

    def render_project(self, project) -> None:
        if project:
            self.project_label.setText(f"项目: {project.get('name', '')} (id={project.get('id', '')})")
        else:
            self.project_label.setText("未选择项目")

    def close(self) -> None:
        self._subscriptions.close()
        self.api_indicator._timer.stop()
