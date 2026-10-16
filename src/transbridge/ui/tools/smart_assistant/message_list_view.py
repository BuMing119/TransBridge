from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLayout, QPushButton, QScrollArea, QWidget

from .history_navigation import HistoryNavigation
from .message_bubble import MessageBubble
from .message_history_view import MessageHistory, visible_record
from .message_scroll import MessageScrollFollowing
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


def _history_record(widget):
    if isinstance(widget, MessageBubble):
        return {"role": widget.role, "content": widget.text, "message_id": widget.property("message_id") or ""}
    if isinstance(widget, _SystemMessageFrame):
        return {"role": "system", "content": widget._text, "message_id": widget.property("message_id") or ""}
    return None


class MessageListView(QObject):
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
        history_button: QPushButton | None = None,
        history_provider=None,
    ) -> None:
        super().__init__(timer_parent)
        self._layout = layout
        self._scroll_area = scroll_area
        self._back_to_bottom_button = back_to_bottom_button
        self._max_visible_widgets = max_visible_widgets
        self._theme = theme or SmartAssistantTheme()
        self._owned_widgets: list[QWidget] = []
        self._page_widgets: list[QWidget] = []
        self._attachments: dict[QWidget, tuple[str, ...]] = {}
        self._closed = False
        self._scopes = {}
        self._history = MessageHistory(history_button, history_provider)
        self.navigation = HistoryNavigation(
            self._history_snapshot,
            page_size=max_visible_widgets,
            earlier_button=history_button,
            parent=timer_parent,
        )
        self.navigation.apply_theme(self._theme)
        self.navigation.page_requested.connect(self._show_page)
        self.navigation.latest_requested.connect(self._restore_live)
        self._locate_timer = QTimer(self)
        self._locate_timer.setSingleShot(True)
        self._locate_timer.timeout.connect(self._locate_page)
        self._scroll_following = MessageScrollFollowing(
            scroll_area, back_to_bottom_button, history_active=lambda: self.navigation.active, parent=self
        )
        self._thinking_indicator: ThinkingIndicator | None = None
        self._back_to_bottom_button.clicked.connect(self.scroll_to_bottom)

    @property
    def closed(self) -> bool:
        return self._closed

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme
        self.navigation.apply_theme(theme)
        for widget in (*self._owned_widgets, *self._page_widgets, *self._attachments):
            apply_theme = getattr(widget, "apply_theme", None)
            if apply_theme is not None:
                apply_theme(theme)

    def contains(self, widget: QWidget) -> bool:
        return not self._closed and (widget in self._owned_widgets or self._layout.indexOf(widget) >= 0)

    def add_bubble(self, bubble: MessageBubble) -> None:
        bubble.apply_theme(self._theme)
        identity = bubble.property("message_id")
        if identity in self._scopes:
            bubble.set_scope(self._scopes[identity])
        self.add_widget(bubble)

    def set_scopes(self, scopes) -> None:
        self._scopes = scopes
        for widget in (*self._owned_widgets, *self._page_widgets):
            if isinstance(widget, MessageBubble):
                widget.set_scope(scopes.get(widget.property("message_id")))

    def add_widget(self, widget: QWidget) -> None:
        if self._closed:
            widget.deleteLater()
            return
        self._owned_widgets.append(widget)
        if self.navigation.active:
            widget.setParent(self._scroll_area.widget())
            widget.hide()
        else:
            self._layout.insertWidget(self._layout.count() - 1, widget)
        self._enforce_limit()
        self.navigation.records_changed(
            has_earlier=bool(self._history.records), new_message=_history_record(widget) is not None
        )
        self._position_attachments()
        self.follow_tail()

    def add_system_message(self, text: str) -> None:
        if not self._closed:
            self.add_widget(_SystemMessageFrame(text, theme=self._theme))

    def load_history(self, messages: list[dict]) -> None:
        if self._closed:
            return
        records = [record for message in messages if (record := visible_record(message)) is not None]
        if len(records) > self._max_visible_widgets:
            for widget in tuple(self._owned_widgets):
                self._history.remember(_history_record(widget))
                self.remove(widget)
            for record in records[: -self._max_visible_widgets]:
                self._history.remember(record)
            records = records[-self._max_visible_widgets :]
        for message in records:
            role = message.get("role", "user")
            content = message.get("content", "")
            bubble = None
            if role == "assistant":
                if content:
                    bubble = MessageBubble(content, "assistant", theme=self._theme)
            elif role == "user":
                if content.startswith("【工具执行结果") or content.startswith("【计划执行完成】"):
                    bubble = _SystemMessageFrame(content, theme=self._theme)
                else:
                    bubble = MessageBubble(content, "user", theme=self._theme)
            elif role == "system":
                bubble = _SystemMessageFrame(content, theme=self._theme)
            if bubble is not None:
                bubble.setProperty("message_id", message.get("message_id", ""))
                self.add_bubble(bubble) if isinstance(bubble, MessageBubble) else self.add_widget(bubble)

    def show_history(self):
        if not self._closed:
            self.navigation.show_earlier()

    def _history_snapshot(self):
        return self._history.snapshot(_history_record(widget) for widget in self._owned_widgets)

    def search_history(self, query: str) -> int:
        return 0 if self._closed else self.navigation.search(query)

    def jump_to_message(self, message_id: str) -> bool:
        return not self._closed and self.navigation.jump_to_message(message_id)

    def _show_page(self, records, target):
        if self._closed:
            return
        self._scroll_following.pause()
        self._clear_page()
        for widget in self._owned_widgets:
            self._layout.removeWidget(widget)
            widget.hide()
        for record in records:
            role, content = record["role"], record["content"]
            widget = (
                _SystemMessageFrame(content, theme=self._theme)
                if role == "system"
                else MessageBubble(content, role, theme=self._theme)
            )
            widget.setProperty("message_id", record.get("message_id", ""))
            widget.setProperty("history_match", record.get("history_match", False))
            if isinstance(widget, MessageBubble):
                widget.set_scope(self._scopes.get(record.get("message_id")))
                widget.set_history_highlight(bool(record.get("history_match")))
            self._page_widgets.append(widget)
            self._layout.insertWidget(self._layout.count() - 1, widget)
        self._position_attachments()
        self._locate_timer.start(0)

    def _locate_page(self):
        if self._closed or not self.navigation.active:
            return
        target = next((w for w in self._page_widgets if w.property("history_match")), None)
        if target is not None:
            self._scroll_area.ensureWidgetVisible(target, 0, 20)
        else:
            self._scroll_area.verticalScrollBar().setValue(0)
        self._scroll_following.update_button()

    def _clear_page(self):
        self._locate_timer.stop()
        for widget in self._page_widgets:
            self._layout.removeWidget(widget)
            widget.hide()
            widget.deleteLater()
        self._page_widgets.clear()

    def _restore_live(self):
        if self._closed:
            return
        self._clear_page()
        for widget in self._owned_widgets:
            self._layout.insertWidget(self._layout.count() - 1, widget)
            widget.show()
        self._position_attachments()
        self.scroll_to_bottom()

    def attach(self, widget: QWidget, source_ids: tuple[str, ...] = ()) -> None:
        """Place a presenter-owned projection with its source turn, without taking lifetime ownership."""
        if self._closed:
            return
        self._attachments[widget] = source_ids
        self._position_attachments()

    def detach(self, widget: QWidget) -> None:
        self._attachments.pop(widget, None)
        self._layout.removeWidget(widget)
        widget.hide()

    def _position_attachments(self) -> None:
        for widget, sources in self._attachments.items():
            self._layout.removeWidget(widget)
            index = self._layout.count() - 1
            candidates = [self._layout.itemAt(offset).widget() for offset in range(index)]
            anchor = max(
                (
                    offset
                    for offset, candidate in enumerate(candidates)
                    if candidate is not None and candidate.property("message_id") in sources
                ),
                default=-1,
            )
            if anchor < 0:
                # Requests without a visible source remain accessible in task details.
                widget.hide()
                continue
            for offset, candidate in enumerate(candidates):
                if (
                    anchor >= 0
                    and offset > anchor
                    and isinstance(candidate, MessageBubble)
                    and candidate.role == "user"
                ):
                    index = offset
                    break
            self._layout.insertWidget(index, widget)
            widget.show()

    def remove(self, widget: QWidget) -> None:
        if self._closed:
            return
        index = self._layout.indexOf(widget)
        if index >= 0 or widget in self._owned_widgets:
            self._layout.removeWidget(widget)
            if widget in self._owned_widgets:
                self._owned_widgets.remove(widget)
            widget.deleteLater()

    def clear(self) -> None:
        if self._closed:
            return
        self.hide_thinking()
        self._clear_page()
        for widget in tuple(self._attachments):
            self.detach(widget)
        for widget in tuple(self._owned_widgets):
            self._layout.removeWidget(widget)
            widget.deleteLater()
        self._owned_widgets.clear()
        self._history.clear()
        self.navigation.reset()
        self._scopes = {}
        self._scroll_following.reset()

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
            self._scroll_following.close()
            self._closed = True

    def scroll_to_bottom(self) -> None:
        if self._closed:
            return
        if self.navigation.active:
            self.navigation.show_latest()
            return
        self._scroll_following.bottom()

    def follow_tail(self) -> None:
        self._scroll_following.follow()

    def reposition_later(self) -> None:
        self._scroll_following.reposition()

    def _enforce_limit(self) -> None:
        while len(self._owned_widgets) > self._max_visible_widgets:
            widget = self._owned_widgets[0]
            self._history.remember(_history_record(widget))
            self._layout.removeWidget(widget)
            self._owned_widgets.pop(0)
            widget.deleteLater()


__all__ = ["MessageListView"]
