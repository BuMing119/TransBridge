from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel

from .message_bubble import MessageBubble
from .message_list_view import MessageListView


class StreamingPresenter:
    """Applies coalesced stream snapshots to the active message bubble."""

    def __init__(self, message_list: MessageListView) -> None:
        self._message_list = message_list
        self._closed = False
        self._generation = 0

    @property
    def generation(self) -> int:
        return self._generation

    def advance_generation(self) -> int:
        self._generation += 1
        return self._generation

    def flush(self, text: str, bubble: MessageBubble) -> None:
        if self._closed or not self._message_list.contains(bubble):
            return
        wrapper = bubble._content_wrapper
        if wrapper is None:
            return
        layout = wrapper.layout()
        if layout is None:
            return
        if bubble._content is not None and not isinstance(bubble._content, QLabel):
            layout.removeWidget(bubble._content)
            bubble._content.deleteLater()
            bubble._content = None
        if bubble._content is None:
            bubble._content = QLabel(text)
            bubble._content.setWordWrap(True)
            bubble._content.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(bubble._content)
        else:
            bubble._content.setText(text)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.advance_generation()


__all__ = ["StreamingPresenter"]
