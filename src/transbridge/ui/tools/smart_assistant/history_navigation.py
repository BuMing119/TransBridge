"""In-conversation paging and full-history search, without restoring old execution controls."""

from contextlib import contextmanager
import logging
import weakref

from PyQt6 import sip
from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from transbridge.ui.foundation.tabler_icons import tabler_icon

from .theme_support import QUIET_BUTTON_STYLE

logger = logging.getLogger(__name__)


class HistoryNavigation(QWidget):
    page_requested = pyqtSignal(object, str)
    latest_requested = pyqtSignal()

    def __init__(self, snapshot, *, page_size, earlier_button=None, parent=None):
        super().__init__(parent)
        self._snapshot = snapshot
        self.page_size = page_size
        self.records = []
        self.start = 0
        self.active = False
        self._new_messages = 0
        self._has_earlier = False
        self._hits = []
        self._hit = -1
        self._notice = ""
        self._return_focus = None
        self._replacing_records = False
        self._searched_text = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._pages = QWidget()
        pages = QHBoxLayout(self._pages)
        pages.setContentsMargins(0, 0, 0, 0)
        self.earlier = earlier_button or QPushButton("更早消息")
        self.earlier.setText("更早消息")
        self.newer = QPushButton("更新消息")
        self.latest = QPushButton("回到最新")
        self.search_toggle = QPushButton("搜索历史", self)
        self.search_toggle.setCheckable(True)
        self.search_toggle.hide()  # Kept for callers; the conversation menu owns this entry point.
        self.status = QLabel("当前为最新消息")
        self.status.setWordWrap(True)
        for button in (self.earlier, self.newer, self.latest):
            pages.addWidget(button)
        pages.addStretch()
        layout.addWidget(self._pages)
        self.search_panel = QWidget()
        search = QHBoxLayout(self.search_panel)
        search.setContentsMargins(0, 0, 0, 0)
        search.setSpacing(4)
        self.query = QLineEdit()
        self.query.setPlaceholderText("搜索全部会话消息")
        self.query.setAccessibleName("搜索完整会话历史")
        self.query.setClearButtonEnabled(True)
        self.query.setMinimumWidth(160)
        self.find = QPushButton()
        self.previous_match = QPushButton()
        self.next_match = QPushButton()
        self.close_search_button = QPushButton()
        self._search_actions = (
            (self.find, "search", "搜索完整会话历史"),
            (self.previous_match, "chevron-left", "定位上一处匹配消息"),
            (self.next_match, "chevron-right", "定位下一处匹配消息"),
            (self.close_search_button, "x", "关闭历史搜索（Esc），保留当前阅读位置"),
        )
        search.addWidget(self.query, 1)
        for button, icon, description in self._search_actions:
            button.setFixedSize(30, 30)
            button.setIconSize(QSize(14, 14))
            button.setIcon(tabler_icon(button, icon, 14))
            button.setStyleSheet(QUIET_BUTTON_STYLE)
            button.setAccessibleName(description)
            button.setToolTip(description)
            search.addWidget(button)
        layout.addWidget(self.search_panel)
        layout.addWidget(self.status)
        self.earlier.clicked.connect(self.show_earlier)
        self.newer.clicked.connect(self.show_newer)
        self.latest.clicked.connect(self.show_latest)
        self.find.clicked.connect(self.search)
        self.query.returnPressed.connect(self.search)
        self.previous_match.clicked.connect(self.previous_hit)
        self.next_match.clicked.connect(self.next_hit)
        self.query.textChanged.connect(self._query_changed)
        self.search_toggle.toggled.connect(self._toggle_search)
        self.close_search_button.clicked.connect(self.close_search)
        self._escape = QShortcut(QKeySequence("Escape"), self)
        self._escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._escape.activated.connect(self.close_search)
        self.reset()

    def _read(self):
        try:
            self.records = self._snapshot()
            self._notice = ""
            return True
        except Exception:
            logger.warning("Unable to read complete conversation history", exc_info=True)
            self._notice = "无法读取会话历史，请重试。"
            self._update()
            return False

    def reset(self):
        if self._replacing_records:
            self._has_earlier = False
            return
        self.records = []
        self.active = False
        self._has_earlier = False
        self.start = self._new_messages = 0
        self._hits, self._hit = [], -1
        self._notice = ""
        self._searched_text = None
        self._return_focus = None
        self.search_toggle.setChecked(False)
        self.search_panel.hide()
        self.query.clear()
        self._update()

    def records_changed(self, *, has_earlier, new_message=False):
        self._has_earlier = has_earlier
        if self._replacing_records:
            return
        if self.active and new_message:
            self._new_messages += 1
            self.latest.setAccessibleDescription("新消息已到达，当前历史页保持不变")
        self._update()

    @contextmanager
    def replacing_records(self):
        """Reload this conversation without resetting its search editor or reading page."""
        previous_count = len(self.records)
        target = self.records[self._hits[self._hit]] if 0 <= self._hit < len(self._hits) else None
        self._replacing_records = True
        try:
            yield
        finally:
            self._replacing_records = False
            self._refresh_records(previous_count, target)

    def _refresh_records(self, previous_count, target):
        if not self.active and self._searched_text is None:
            self._update()
            return
        if not self._read():
            return
        if self.active:
            self._new_messages += max(0, len(self.records) - previous_count)
        target_index = None
        if self._searched_text is not None:
            self._hits = [
                index
                for index, record in enumerate(self.records)
                if self._searched_text in record["content"].casefold()
            ]
            self._hit = min(max(0, self._hit), len(self._hits) - 1)
            if self._hits:
                if target is not None:
                    self._hit = next(
                        (
                            offset
                            for offset, index in enumerate(self._hits)
                            if self.records[index] == target
                            or (
                                target.get("message_id")
                                and self.records[index].get("message_id") == target["message_id"]
                            )
                        ),
                        self._hit,
                    )
                target_index = self._hits[self._hit]
            else:
                self._notice = "未找到匹配消息"
        if self.active:
            self.start = min(self.start, max(0, len(self.records) - self.page_size))
            self._show(target_index=target_index)
        else:
            self._update()

    def open_search(self):
        """Reveal search from a conversation menu or its scoped keyboard shortcut."""
        self.search_toggle.setChecked(True)
        self.query.setFocus()
        self.query.selectAll()

    def close_search(self):
        """Dismiss only the search controls; never replace the current message page."""
        self._notice = ""
        self.search_toggle.setChecked(False)
        self._update()
        previous = self._return_focus() if self._return_focus is not None else None
        self._return_focus = None
        if previous is not None and not sip.isdeleted(previous) and previous.isVisible() and previous.isEnabled():
            previous.setFocus(Qt.FocusReason.OtherFocusReason)

    def show_earlier(self):
        if not self._read():
            return
        end = self.start if self.active else max(0, len(self.records) - self.page_size)
        self.start = max(0, end - self.page_size)
        self._show()

    def show_newer(self):
        if not self._read():
            return
        self.start += self.page_size
        if self.start >= max(0, len(self.records) - self.page_size):
            self.show_latest()
        else:
            self._show()

    def show_latest(self):
        self.active = False
        self._new_messages = 0
        self._update()
        self.latest_requested.emit()

    def search(self, query=None):
        if isinstance(query, str):
            self.open_search()
            self.query.setText(query)
        text = self.query.text().strip().casefold()
        if not self._read():
            return 0
        self._searched_text = text or None
        self._hits = [
            index for index, record in enumerate(self.records) if text and text in record["content"].casefold()
        ]
        self._hit = 0 if self._hits else -1
        if self._hits:
            self._locate(self._hits[0])
        else:
            self._notice = "未找到匹配消息" if text else "输入关键词后搜索全部消息"
            self._update()
        return len(self._hits)

    def previous_hit(self):
        if self._hit > 0:
            self._hit -= 1
            self._locate(self._hits[self._hit])

    def next_hit(self):
        if self._hit + 1 < len(self._hits):
            self._hit += 1
            self._locate(self._hits[self._hit])

    def jump_to_message(self, message_id):
        if not message_id:
            return False
        if not self._read():
            return False
        index = next((i for i, record in enumerate(self.records) if record.get("message_id") == message_id), None)
        if index is None:
            self._notice = "该消息不在当前会话可见历史中。"
            self._update()
            return False
        self.query.clear()
        self._hits, self._hit = [], -1
        self._locate(index)
        return True

    def _locate(self, index):
        self.start = max(0, min(index - self.page_size // 2, len(self.records) - self.page_size))
        record = self.records[index]
        self._show(record.get("message_id", ""), target_index=index)

    def _show(self, target="", *, target_index=None):
        self.active = True
        end = min(len(self.records), self.start + self.page_size)
        self._update()
        # Anonymous notices can be located by index without inventing a canonical message ID.
        page = [
            dict(record, history_match=self.start + offset == target_index)
            for offset, record in enumerate(self.records[self.start : end])
        ]
        self.page_requested.emit(page, target)

    def _query_changed(self):
        self._notice = ""
        self._searched_text = None
        self._hits, self._hit = [], -1
        self._update()

    def _toggle_search(self, expanded):
        if expanded:
            focused = QApplication.focusWidget()
            if focused is not None and focused is not self and not self.isAncestorOf(focused):
                self._return_focus = weakref.ref(focused)
        self.search_panel.setVisible(expanded)
        self._update()
        if expanded:
            self.query.setFocus()

    def _update(self):
        expanded = self.search_toggle.isChecked()
        earlier = self.start > 0 if self.active else self._has_earlier
        self.earlier.setEnabled(earlier)
        self.earlier.setVisible(earlier)
        self.newer.setEnabled(self.active)
        self.latest.setEnabled(self.active)
        self.newer.setVisible(self.active)
        self.latest.setVisible(self.active)
        self._pages.setVisible(earlier or self.active)
        self.latest.setText(f"回到最新 · {self._new_messages} 条新消息" if self._new_messages else "回到最新")
        self.previous_match.setEnabled(self._hit > 0)
        self.next_match.setEnabled(0 <= self._hit < len(self._hits) - 1)
        page = (
            f"历史 {self.start + 1}–{min(self.start + self.page_size, len(self.records))} / 共 {len(self.records)} 条"
            if self.active
            else "当前为最新消息"
        )
        match = f" · 匹配 {self._hit + 1}/{len(self._hits)}" if self._hits and expanded else ""
        self.status.setText(self._notice or page + match)
        self.status.setVisible(self.active or expanded or bool(self._notice))
        self._escape.setEnabled(expanded)
        self.setVisible(earlier or self.active or expanded or bool(self._notice))

    def apply_theme(self, theme):
        theme.apply_surface(self)
        theme.apply_semantic(self.status, "muted")
        for button in (
            self.earlier,
            self.newer,
            self.latest,
            self.search_toggle,
            self.find,
            self.previous_match,
            self.next_match,
            self.close_search_button,
        ):
            button.setStyleSheet(QUIET_BUTTON_STYLE)
            theme.apply_surface(button)
        for button, icon, _description in self._search_actions:
            button.setIcon(tabler_icon(button, icon, 14))
