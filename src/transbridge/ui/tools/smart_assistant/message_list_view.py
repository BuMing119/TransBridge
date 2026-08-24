from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QWidget,
)

from .message_bubble import MessageBubble
from .thinking_indicator import ThinkingIndicator


class MessageListView:
    """Owns widgets rendered in the chat message stream.

    The surrounding ``ChatWidget`` remains the public facade; this object keeps
    list mutation, eviction and history rendering behind one lifecycle guard.
    """

    def __init__(
        self,
        layout: QLayout,
        *,
        scroll_area: QScrollArea,
        back_to_bottom_button: QPushButton,
        timer_parent: QWidget,
        max_visible_widgets: int,
    ) -> None:
        self._layout = layout
        self._scroll_area = scroll_area
        self._back_to_bottom_button = back_to_bottom_button
        self._max_visible_widgets = max_visible_widgets
        self._closed = False
        self._thinking_indicator: ThinkingIndicator | None = None
        self._pending_scroll_value = 0
        self._scroll_timer = QTimer(timer_parent)
        self._scroll_timer.setInterval(100)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.timeout.connect(self._update_scroll_button)
        self._back_to_bottom_button.clicked.connect(self.scroll_to_bottom)
        scrollbar = self._scroll_area.verticalScrollBar()
        if scrollbar is not None:
            scrollbar.valueChanged.connect(self._on_scroll_changed)

    @property
    def closed(self) -> bool:
        return self._closed

    def contains(self, widget: QWidget) -> bool:
        return not self._closed and self._layout.indexOf(widget) >= 0

    def add_bubble(self, bubble: MessageBubble) -> None:
        self.add_widget(bubble)

    def add_widget(self, widget: QWidget) -> None:
        if self._closed:
            widget.deleteLater()
            return
        self._layout.insertWidget(self._layout.count() - 1, widget)
        self.scroll_to_bottom()
        self._enforce_limit()

    def add_system_message(self, text: str) -> None:
        if self._closed:
            return
        is_ok = text.startswith("[OK]")
        is_fail = text.startswith("[FAIL]")
        if is_ok:
            color, background = "#388E3C", "#E8F5E9"
        elif is_fail:
            color, background = "#D32F2F", "#FFEBEE"
        else:
            color, background = "#757575", "#F5F5F5"
        frame = QFrame()
        frame.setStyleSheet(
            "QFrame {"
            f"  border-left: 3px solid {color};"
            f"  background-color: {background};"
            "  border-radius: 4px; padding: 4px 10px; margin: 2px 0;"
            "}"
        )
        frame_layout = QHBoxLayout(frame)
        frame_layout.setContentsMargins(8, 4, 8, 4)
        label = QLabel(text)
        label.setStyleSheet("color: #333; font-size: 11px; border: none; background: transparent;")
        label.setWordWrap(True)
        frame_layout.addWidget(label)
        self.add_widget(frame)

    def load_history(self, messages: list[dict]) -> None:
        if self._closed:
            return
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            if role == "assistant":
                self.add_bubble(MessageBubble(content, "assistant"))
            elif role == "user":
                if content.startswith("【工具执行结果") or content.startswith("【计划执行完成】"):
                    self.add_system_message(content)
                else:
                    self.add_bubble(MessageBubble(content, "user"))

    def remove(self, widget: QWidget) -> None:
        if self._closed:
            return
        index = self._layout.indexOf(widget)
        if index >= 0:
            self._layout.removeWidget(widget)
            widget.deleteLater()

    def clear(self) -> None:
        if self._closed:
            return
        self.hide_thinking()
        count = self._layout.count()
        while count > 1:
            item = self._layout.takeAt(count - 2)
            if item.widget() is not None:
                item.widget().deleteLater()
            count -= 1

    def show_thinking(self, thought: str) -> None:
        if self._closed:
            return
        self.hide_thinking()
        indicator = ThinkingIndicator()
        indicator.set_thought(thought)
        self._thinking_indicator = indicator
        self.add_widget(indicator)

    def hide_thinking(self) -> None:
        indicator = self._thinking_indicator
        if indicator is None:
            return
        indicator.stop_animation()
        self._thinking_indicator = None
        self.remove(indicator)

    def toggle_thinking(self) -> None:
        indicator = self._thinking_indicator
        if indicator is not None and indicator.isVisible():
            indicator.toggle_expand()

    def close(self) -> None:
        if self._closed:
            return
        self.clear()
        self._scroll_timer.stop()
        self._closed = True

    def scroll_to_bottom(self) -> None:
        if self._closed:
            return
        scrollbar = self._scroll_area.verticalScrollBar()
        if scrollbar is not None:
            scrollbar.setValue(scrollbar.maximum())
        self._back_to_bottom_button.setVisible(False)

    def reposition_later(self) -> None:
        if not self._closed and self._back_to_bottom_button.isVisible():
            QTimer.singleShot(0, self._reposition_button)

    def _on_scroll_changed(self, value: int) -> None:
        if self._closed:
            return
        self._pending_scroll_value = value
        if not self._scroll_timer.isActive():
            self._scroll_timer.start()

    def _update_scroll_button(self) -> None:
        if self._closed:
            return
        scrollbar = self._scroll_area.verticalScrollBar()
        if scrollbar is None:
            return
        if scrollbar.maximum() > 0 and self._pending_scroll_value < scrollbar.maximum() - 50:
            self._reposition_button()
            self._back_to_bottom_button.setVisible(True)
        else:
            self._back_to_bottom_button.setVisible(False)

    def _reposition_button(self) -> None:
        try:
            self._back_to_bottom_button.move(
                self._scroll_area.width() - self._back_to_bottom_button.width() - 12,
                self._scroll_area.height() - self._back_to_bottom_button.height() - 12,
            )
        except RuntimeError:
            pass

    def _enforce_limit(self) -> None:
        while self._layout.count() - 1 > self._max_visible_widgets:
            item = self._layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()


__all__ = ["MessageListView"]
