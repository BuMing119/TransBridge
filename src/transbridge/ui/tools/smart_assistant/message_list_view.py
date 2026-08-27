from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLayout, QPushButton, QScrollArea, QWidget

from .message_bubble import MessageBubble
from .theme_support import CARD_STRUCTURE_STYLE, SmartAssistantTheme
from .thinking_indicator import ThinkingIndicator


class _SystemMessageFrame(QFrame):
    def __init__(self, text: str, *, theme: SmartAssistantTheme) -> None:
        super().__init__()
        self._text = text
        self._state = "success" if text.startswith("[OK]") else "error" if text.startswith("[FAIL]") else "muted"
        self.setProperty("tbSurface", "card")
        self.setStyleSheet(CARD_STRUCTURE_STYLE)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        self._label = QLabel(text)
        self._label.setWordWrap(True)
        layout.addWidget(self._label)
        self.apply_theme(theme)

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        theme.apply_semantic(self, self._state, background=True)
        theme.apply_semantic(self._label, self._state)
        theme.mark_status(self, self._text, self._state)


class MessageListView:
    """Owns stream widgets and refreshes only explicitly owned presentation objects."""

    def __init__(
        self,
        layout: QLayout,
        *,
        scroll_area: QScrollArea,
        back_to_bottom_button: QPushButton,
        timer_parent: QWidget,
        max_visible_widgets: int,
        theme: SmartAssistantTheme | None = None,
    ) -> None:
        self._layout = layout
        self._scroll_area = scroll_area
        self._back_to_bottom_button = back_to_bottom_button
        self._max_visible_widgets = max_visible_widgets
        self._theme = theme or SmartAssistantTheme()
        self._owned_widgets: list[QWidget] = []
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

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme
        for widget in tuple(self._owned_widgets):
            apply_theme = getattr(widget, "apply_theme", None)
            if apply_theme is not None:
                apply_theme(theme)

    def contains(self, widget: QWidget) -> bool:
        return not self._closed and self._layout.indexOf(widget) >= 0

    def add_bubble(self, bubble: MessageBubble) -> None:
        bubble.apply_theme(self._theme)
        self.add_widget(bubble)

    def add_widget(self, widget: QWidget) -> None:
        if self._closed:
            widget.deleteLater()
            return
        self._owned_widgets.append(widget)
        self._layout.insertWidget(self._layout.count() - 1, widget)
        self.scroll_to_bottom()
        self._enforce_limit()

    def add_system_message(self, text: str) -> None:
        if not self._closed:
            self.add_widget(_SystemMessageFrame(text, theme=self._theme))

    def load_history(self, messages: list[dict]) -> None:
        if self._closed:
            return
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            if role == "assistant":
                if content:
                    self.add_bubble(MessageBubble(content, "assistant", theme=self._theme))
            elif role == "user":
                if content.startswith("【工具执行结果") or content.startswith("【计划执行完成】"):
                    self.add_system_message(content)
                else:
                    self.add_bubble(MessageBubble(content, "user", theme=self._theme))
            elif role == "tool":
                summary = message.get("display_summary", "")
                if summary:
                    self.add_system_message(summary)

    def remove(self, widget: QWidget) -> None:
        if self._closed:
            return
        index = self._layout.indexOf(widget)
        if index >= 0:
            self._layout.removeWidget(widget)
            if widget in self._owned_widgets:
                self._owned_widgets.remove(widget)
            widget.deleteLater()

    def clear(self) -> None:
        if self._closed:
            return
        self.hide_thinking()
        for widget in tuple(self._owned_widgets):
            self._layout.removeWidget(widget)
            widget.deleteLater()
        self._owned_widgets.clear()

    def show_thinking(self, thought: str) -> None:
        if self._closed:
            return
        self.hide_thinking()
        indicator = ThinkingIndicator(theme=self._theme)
        indicator.set_thought(thought)
        self._thinking_indicator = indicator
        self.add_widget(indicator)

    def hide_thinking(self) -> None:
        indicator = self._thinking_indicator
        if indicator is not None:
            indicator.stop_animation()
            self._thinking_indicator = None
            self.remove(indicator)

    def toggle_thinking(self) -> None:
        if self._thinking_indicator is not None and self._thinking_indicator.isVisible():
            self._thinking_indicator.toggle_expand()

    def close(self) -> None:
        if not self._closed:
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
        if not self._closed:
            self._pending_scroll_value = value
            if not self._scroll_timer.isActive():
                self._scroll_timer.start()

    def _update_scroll_button(self) -> None:
        if self._closed:
            return
        scrollbar = self._scroll_area.verticalScrollBar()
        if scrollbar is None:
            return
        visible = scrollbar.maximum() > 0 and self._pending_scroll_value < scrollbar.maximum() - 50
        if visible:
            self._reposition_button()
        self._back_to_bottom_button.setVisible(visible)

    def _reposition_button(self) -> None:
        try:
            self._back_to_bottom_button.move(
                self._scroll_area.width() - self._back_to_bottom_button.width() - 12,
                self._scroll_area.height() - self._back_to_bottom_button.height() - 12,
            )
        except RuntimeError:
            pass

    def _enforce_limit(self) -> None:
        while len(self._owned_widgets) > self._max_visible_widgets:
            widget = self._owned_widgets[0]
            self._layout.removeWidget(widget)
            self._owned_widgets.pop(0)
            widget.deleteLater()


__all__ = ["MessageListView"]
