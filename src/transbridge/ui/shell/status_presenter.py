"""Status-bar rendering and context-to-label binding."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QEvent, Qt, QTimer
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QMainWindow, QStatusBar, QWidget

from transbridge.ui.foundation.components import (
    ComponentKind,
    ComponentStyle,
    ElidedLabel,
    SemanticState,
    StatusBadge,
    reserve_text_width,
)
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


class _MessageToast(QFrame):
    """Short-lived application feedback that does not consume a bottom row."""

    _DISPLAY_MSEC = 5_000
    _MAXIMUM_WIDTH = 480
    _MINIMUM_WIDTH = 260
    _MARGIN = 20

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        ComponentStyle.apply_static(self, ComponentKind.NOTIFICATION)
        ComponentStyle.apply_state(self, SemanticState.INFO)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAccessibleName("应用通知")
        self.setFixedHeight(46)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        self._label = ElidedLabel(parent=self)
        self._label.setAccessibleName("应用通知内容")
        layout.addWidget(self._label, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self._DISPLAY_MSEC)
        self._timer.timeout.connect(self.hide)
        parent.installEventFilter(self)
        self.hide()

    def show_message(self, message: str) -> None:
        self._label.set_full_text(message)
        self._label.setToolTip(message)
        self.setAccessibleDescription(message)
        parent = self.parentWidget()
        if parent is None:
            return
        desired = self._label.fontMetrics().horizontalAdvance(message) + 40
        available = max(1, parent.width() - (self._MARGIN * 2))
        width = min(available, max(self._MINIMUM_WIDTH, min(desired, self._MAXIMUM_WIDTH)))
        self.setFixedWidth(width)
        self._reposition()
        self.show()
        self.raise_()
        self._timer.start()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override
        if watched is self.parentWidget() and event.type() in {QEvent.Type.Resize, QEvent.Type.Show}:
            self._reposition()
        return super().eventFilter(watched, event)

    def shutdown(self) -> None:
        self._timer.stop()
        parent = self.parentWidget()
        if parent is not None:
            parent.removeEventFilter(self)
        self.hide()

    def _reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        x = max(self._MARGIN, parent.width() - self.width() - self._MARGIN)
        self.move(x, self._MARGIN)


class StatusPresenter:
    def __init__(self, window: QMainWindow, context) -> None:
        self._window = window
        self._context = context
        self._subscriptions = SubscriptionGroup()
        status_bar = QStatusBar(window)
        status_bar.setSizeGripEnabled(False)
        window.setStatusBar(status_bar)
        self.status_bar = status_bar
        status_bar.hide()

        # User and project context now have stable homes in the navigation rail
        # and workbench header.  Keep these projections for compatibility, but
        # do not duplicate them in the narrow global status bar.
        self.user_label = ElidedLabel("未登录", status_bar)
        self.user_label.setFixedWidth(150)
        self.user_label.hide()
        self.project_label = ElidedLabel("未选择项目", status_bar)
        self.project_label.setFixedWidth(260)
        self.project_label.hide()

        self.message_label = ElidedLabel("就绪", status_bar)
        self.message_label.setAccessibleName("应用状态")
        self.message_label.setToolTip("就绪")
        self.message_label.hide()
        self.api_indicator = ApiStatusIndicator(status_bar)
        self.api_indicator.hide()
        toast_parent = window.centralWidget() or window
        self._message_toast = _MessageToast(toast_parent)

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
        self.message_label.set_full_text(message)
        self.message_label.setToolTip(message)
        self._message_toast.show_message(message)

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
        self._message_toast.shutdown()
