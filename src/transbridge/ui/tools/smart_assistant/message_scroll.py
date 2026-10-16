"""Scroll following is independent from message ownership and history paging."""

from PyQt6.QtCore import QObject, QTimer, pyqtSlot


class MessageScrollFollowing(QObject):
    def __init__(self, scroll, button, *, history_active, parent):
        super().__init__(parent)
        self.scroll, self.button = scroll, button
        self.history_active = history_active
        self.following = True
        self._moving = self._closed = False
        self._tail = QTimer(self)
        self._tail.setSingleShot(True)
        self._tail.timeout.connect(self._scroll_tail)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(100)
        self._debounce.timeout.connect(self.update_button)
        bar = scroll.verticalScrollBar()
        bar.valueChanged.connect(self._changed)
        bar.rangeChanged.connect(self._range_changed)

    def pause(self):
        self.following = False
        self._tail.stop()

    def reset(self):
        self._tail.stop()
        self._debounce.stop()
        self.following = True
        self.button.hide()

    def bottom(self):
        if not self._closed:
            self.following = True
            self._scroll_tail()
            self._tail.start(0)

    def follow(self):
        if not self._closed:
            if self.following and not self.history_active():
                self._tail.start(0)
            else:
                self.update_button()

    @pyqtSlot(int, int)
    def _range_changed(self, _minimum, _maximum):
        self.follow()

    @pyqtSlot(int)
    def _changed(self, value):
        if not self._closed:
            if not self._moving and not self.history_active():
                self.following = value >= self.scroll.verticalScrollBar().maximum() - 50
            if not self._debounce.isActive():
                self._debounce.start()

    def _scroll_tail(self):
        if self._closed or not self.following or self.history_active():
            return
        self._moving = True
        self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())
        self._moving = False
        self.button.hide()

    def update_button(self):
        if self._closed:
            return
        bar = self.scroll.verticalScrollBar()
        visible = self.history_active() or bar.maximum() > 0 and bar.value() < bar.maximum() - 50
        self.button.setText("回到最新" if self.history_active() else "回到底部")
        self.button.setVisible(visible)
        if visible:
            self.reposition()

    def reposition(self):
        if not self._closed and self.button.isVisible():
            self.button.move(
                self.scroll.width() - self.button.width() - 12,
                self.scroll.height() - self.button.height() - 12,
            )
            self.button.raise_()

    def close(self):
        self.reset()
        self._closed = True
