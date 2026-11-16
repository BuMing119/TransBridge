# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""
MailsDialog: 私信（Mails）对话框，左侧对话列表 + 右侧消息流。
"""

from PyQt6.QtCore import QSignalBlocker, Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from transbridge.paratranz.api.paratranz_mails_api import ParatranzMailsAPI
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, ElidedLabel, SemanticState

from ..workers import ApiWorker


def _extract_list(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("data", "results", "items"):
            if isinstance(data.get(k), list):
                return data[k]
    return []


def _user_name(user_obj, fallback_id) -> str:
    """从嵌套的 user 对象中提取显示名，优先 nickname，次选 username。"""
    if isinstance(user_obj, dict) and user_obj:
        name = user_obj.get("nickname") or user_obj.get("username")
        if name:
            return str(name)
    return str(fallback_id)


class MailsDialog(QDialog):
    _INBOX_PAGE_SIZE = 100
    _CONVERSATION_PAGE_SIZE = 50

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._workers: list[ApiWorker] = []
        self._current_uid: int | None = None
        self._current_name: str = ""
        self._my_uid = (ctx.current_user or {}).get("id")
        self._mail_gen = 0
        self._conv_gen = 0
        self._mails: list[dict] = []
        self._mails_page = 0
        self._has_more_mails = False
        self._messages: list[dict] = []
        self._before: int | None = None
        self._retry_before: int | None = None
        self._drafts: dict[int, str] = {}
        self._sending_uids: set[int] = set()
        self._closed = False
        self.setWindowTitle("私信")
        self.resize(820, 560)
        self._init_ui()
        self.finished.connect(self._on_finished)
        self._load_mails()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        # 左侧：对话列表
        left = QWidget()
        left.setMinimumWidth(180)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(lambda: self._load_mails())
        new_btn = QPushButton("新建私信")
        new_btn.clicked.connect(self._new_mail)
        for button in (refresh_btn, new_btn):
            ComponentStyle.apply_static(button, ComponentKind.BUTTON)
            button.setAutoDefault(False)
        toolbar.addWidget(refresh_btn)
        toolbar.addStretch()
        toolbar.addWidget(new_btn)
        left_layout.addLayout(toolbar)
        self._conv_list = QListWidget()
        self._conv_list.currentItemChanged.connect(self._on_conv_clicked)
        left_layout.addWidget(self._conv_list, stretch=1)
        self._more_mails_btn = QPushButton("加载更多对话")
        ComponentStyle.apply_static(self._more_mails_btn, ComponentKind.BUTTON)
        self._more_mails_btn.setAutoDefault(False)
        self._more_mails_btn.clicked.connect(lambda: self._load_mails(page=self._mails_page + 1))
        left_layout.addWidget(self._more_mails_btn)
        splitter.addWidget(left)

        # 右侧：对话详情
        right = QWidget()
        right.setMinimumWidth(400)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 0, 0, 0)

        self._conv_label = ElidedLabel("（选择联系人查看对话）")
        conversation_font = QFont(self._conv_label.font())
        conversation_font.setWeight(QFont.Weight.DemiBold)
        self._conv_label.setFont(conversation_font)
        self._conv_label.setAccessibleName("当前私信对话")
        right_layout.addWidget(self._conv_label)
        self._conversation_status = QLabel("")
        self._conversation_status.setWordWrap(True)
        self._conversation_status.setTextFormat(Qt.TextFormat.PlainText)
        right_layout.addWidget(self._conversation_status)
        message_actions = QHBoxLayout()
        message_actions.addStretch()
        self._more_messages_btn = QPushButton("加载更早消息")
        self._more_messages_btn.setEnabled(False)
        self._more_messages_btn.clicked.connect(lambda: self._load_conversation(self._current_uid, before=self._before))
        self._retry_btn = QPushButton("重试加载")
        self._retry_btn.clicked.connect(lambda: self._load_conversation(self._current_uid, before=self._retry_before))
        for button in (self._more_messages_btn, self._retry_btn):
            ComponentStyle.apply_static(button, ComponentKind.BUTTON)
            button.setAutoDefault(False)
            message_actions.addWidget(button)
        self._retry_btn.hide()
        right_layout.addLayout(message_actions)

        # 消息流（滚动区域，最新消息在底部）
        self._msg_scroll = QScrollArea()
        self._msg_scroll.setWidgetResizable(True)
        self._msg_container = QWidget()
        self._msg_layout = QVBoxLayout(self._msg_container)
        self._msg_layout.addStretch()
        self._msg_scroll.setWidget(self._msg_container)
        right_layout.addWidget(self._msg_scroll, stretch=1)

        # 发送区
        right_layout.addWidget(QLabel("消息："))
        self._send_input = QTextEdit()
        self._send_input.setMaximumHeight(80)
        self._send_input.setPlaceholderText("支持 Markdown")
        self._send_input.setEnabled(False)
        right_layout.addWidget(self._send_input)

        send_row = QHBoxLayout()
        send_row.addStretch()
        self._send_btn = QPushButton("发送")
        ComponentStyle.apply_static(self._send_btn, ComponentKind.BUTTON)
        ComponentStyle.apply_state(self._send_btn, SemanticState.PRIMARY)
        self._send_btn.setAutoDefault(False)
        self._send_btn.setEnabled(False)
        self._send_btn.clicked.connect(self._send_message)
        send_row.addWidget(self._send_btn)
        right_layout.addLayout(send_row)

        splitter.addWidget(right)
        splitter.setSizes([240, 560])
        layout.addWidget(splitter)

    def _load_mails(self, *, page: int = 1):
        if self._closed:
            return
        config = self._ctx.config
        self._mail_gen += 1
        gen = self._mail_gen
        self._more_mails_btn.setEnabled(False)

        def _fetch():
            api = ParatranzMailsAPI(token=config.token, config=config)
            try:
                return api.list_mails(page=page, page_size=self._INBOX_PAGE_SIZE)
            finally:
                api.close()

        def _on_done(payload):
            if self._closed or self._mail_gen != gen:
                return
            mails = _extract_list(payload)
            page_count = payload.get("pageCount") if isinstance(payload, dict) else None
            self._mails_page = page
            self._has_more_mails = page < page_count if type(page_count) is int else len(mails) >= self._INBOX_PAGE_SIZE
            self._more_mails_btn.setEnabled(self._has_more_mails)
            if page == 1:
                self._mails.clear()
            self._mails.extend(mails)
            self._on_mails_loaded(self._mails)

        def _on_error(error):
            if not self._closed and self._mail_gen == gen:
                self._more_mails_btn.setEnabled(self._has_more_mails)
                QMessageBox.warning(self, "加载失败", error)

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)

    def _on_mails_loaded(self, mails: list):
        # 以对方用户为维度聚合为对话列表
        seen: dict[int, dict] = {}
        my_uid = self._my_uid
        for mail in mails:
            from_id = mail.get("from")
            to_id = mail.get("to")
            other_id = mail.get("uid", to_id if from_id == my_uid else from_id)
            if other_id is None:
                continue

            # 尝试从嵌套 user 对象取显示名
            if from_id == my_uid:
                other_user = mail.get("toUser") or mail.get("to_user") or {}
            else:
                other_user = mail.get("fromUser") or mail.get("from_user") or {}
            other_name = _user_name(mail.get("user") or other_user, other_id)

            if other_id not in seen:
                seen[other_id] = {"uid": other_id, "name": other_name, "last": mail}
            else:
                if mail.get("createdAt", "") > seen[other_id]["last"].get("createdAt", ""):
                    seen[other_id]["last"] = mail
                # 用更好的名字覆盖（非纯数字）
                if other_name != str(other_id):
                    seen[other_id]["name"] = other_name

        # 按最后消息时间降序排列（最新对话靠上）
        sorted_convs = sorted(
            seen.values(),
            key=lambda info: info["last"].get("createdAt", ""),
            reverse=True,
        )

        scrollbar = self._conv_list.verticalScrollBar()
        scroll_position = scrollbar.value()
        with QSignalBlocker(self._conv_list):
            self._conv_list.clear()
            for info in sorted_convs:
                last = info["last"]
                name = info["name"]
                preview = str(last.get("content", ""))[:30]
                unread_count = last.get("unreadCount")
                unread = unread_count > 0 if type(unread_count) is int else last.get("status") == 0
                item = QListWidgetItem(f"{'[未读] ' if unread else ''}{name}\n{preview}")
                item.setData(Qt.ItemDataRole.UserRole, {"uid": info["uid"], "name": name})
                self._conv_list.addItem(item)
                if info["uid"] == self._current_uid:
                    self._conv_list.setCurrentItem(item)
        scrollbar.setValue(scroll_position)

    def _on_conv_clicked(self, item: QListWidgetItem | None, _previous=None):
        if item is None:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if data is None:
            return
        uid = data["uid"]
        name = data["name"]
        self._activate_conversation(uid, name)
        self._load_conversation(uid)

    def _activate_conversation(self, uid: int, name: str):
        if self._current_uid is not None:
            self._drafts[self._current_uid] = self._send_input.toPlainText()
        self._conv_gen += 1
        self._current_uid = uid
        self._current_name = name
        self._conv_label.set_full_text(name)
        self._conv_label.setToolTip(name)
        self._send_input.setPlainText(self._drafts.get(uid, ""))
        self._send_input.setEnabled(True)
        self._send_btn.setEnabled(uid not in self._sending_uids)
        self._messages.clear()
        self._before = None
        self._retry_before = None
        self._more_messages_btn.setEnabled(False)
        self._clear_messages()
        self._conversation_status.clear()
        self._retry_btn.hide()

    def _load_conversation(self, uid: int | None, *, before: int | None = None):
        if self._closed or uid is None or uid != self._current_uid:
            return
        config = self._ctx.config
        self._conv_gen += 1
        gen = self._conv_gen
        self._conversation_status.setText("正在加载更早消息…" if before is not None else "正在加载对话…")
        self._more_messages_btn.setEnabled(False)
        self._retry_btn.hide()

        def _fetch():
            api = ParatranzMailsAPI(token=config.token, config=config)
            try:
                return api.get_conversation(uid, before=before, limit=self._CONVERSATION_PAGE_SIZE)
            finally:
                api.close()

        def _on_done(messages):
            if self._closed or self._conv_gen != gen:
                return
            self._on_conversation_loaded(messages, before=before)

        def _on_error(error):
            if not self._closed and self._conv_gen == gen:
                self._conversation_status.setText(f"加载对话失败：{error}")
                self._retry_before = before
                self._retry_btn.show()

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)

    def _clear_messages(self):
        while self._msg_layout.count() > 1:
            it = self._msg_layout.takeAt(0)
            if it.widget():
                it.widget().hide()
                it.widget().deleteLater()

    def _on_conversation_loaded(self, payload, *, before: int | None):
        page = _extract_list(payload)
        page_by_id = {msg["id"]: msg for msg in page if type(msg.get("id")) is int and msg["id"] > 0}
        if before is not None:
            older = {msg_id: msg for msg_id, msg in page_by_id.items() if msg_id < before}
            self._messages.extend(older.values())
            self._before = min(older) if older and len(page) >= self._CONVERSATION_PAGE_SIZE else None
        elif page_by_id.keys() & {msg.get("id") for msg in self._messages}:
            # A refresh after sending can overlap already loaded history.
            self._messages = [msg for msg in self._messages if msg.get("id") not in page_by_id] + page
        else:
            self._messages = page
            self._before = min(page_by_id) if page_by_id and len(page) >= self._CONVERSATION_PAGE_SIZE else None
        self._more_messages_btn.setEnabled(self._before is not None)
        self._render_messages(keep_position=before is not None)

    def _render_messages(self, *, keep_position: bool):
        scrollbar = self._msg_scroll.verticalScrollBar()
        offset_from_bottom = scrollbar.maximum() - scrollbar.value() if keep_position else 0
        self._clear_messages()
        my_uid = self._my_uid
        # 按时间升序排列（最新消息在底部）
        msgs = sorted(self._messages, key=lambda msg: (msg.get("createdAt") or "", msg.get("id") or 0))
        self._conversation_status.setText(f"共 {len(msgs)} 条消息" if msgs else "（暂无消息）")
        self._retry_btn.hide()
        for msg in msgs:
            is_mine = msg.get("from") == my_uid
            content = msg.get("html") or msg.get("content") or ""
            created = str(msg.get("createdAt", ""))[:16]

            bubble = QLabel(f"{content}\n<small>{created}</small>")
            bubble.setWordWrap(True)
            bubble.setTextFormat(Qt.TextFormat.RichText)
            bubble.setMaximumWidth(450)
            bubble.setAccessibleName("我发送的私信" if is_mine else "收到的私信")
            bubble.setAccessibleDescription(f"{created}：{content}")
            ComponentStyle.apply_static(bubble, ComponentKind.CARD)
            ComponentStyle.apply_state(bubble, SemanticState.INFO if is_mine else SemanticState.DEFAULT)

            if is_mine:
                row = QHBoxLayout()
                row.addStretch()
                row.addWidget(bubble)
            else:
                row = QHBoxLayout()
                row.addWidget(bubble)
                row.addStretch()

            wrapper = QWidget()
            wrapper.setLayout(row)
            # 插入到 stretch 之前，保持时间顺序从上到下
            self._msg_layout.insertWidget(self._msg_layout.count() - 1, wrapper)

        generation = self._conv_gen

        def _restore_scroll():
            if not self._closed and self._conv_gen == generation:
                scrollbar.setValue(scrollbar.maximum() - offset_from_bottom)

        QTimer.singleShot(0, _restore_scroll)

    def _send_message(self):
        if self._closed or self._current_uid is None or self._current_uid in self._sending_uids:
            return
        draft = self._send_input.toPlainText()
        content = draft.strip()
        if not content:
            return
        config = self._ctx.config
        to_uid = self._current_uid
        name = self._current_name
        self._drafts[to_uid] = draft
        self._sending_uids.add(to_uid)
        self._send_btn.setEnabled(False)

        def _send():
            api = ParatranzMailsAPI(token=config.token, config=config)
            return api.send_mail(to_uid, content)

        def _on_done(_):
            self._sending_uids.discard(to_uid)
            if self._drafts.get(to_uid) == draft:
                self._drafts.pop(to_uid, None)
            if not self._closed and self._current_uid == to_uid:
                if self._send_input.toPlainText() == draft:
                    self._send_input.clear()
                self._send_btn.setEnabled(True)
                self._load_conversation(to_uid)

        def _on_error(error):
            self._sending_uids.discard(to_uid)
            if self._closed:
                return
            if self._current_uid == to_uid:
                self._send_btn.setEnabled(True)
            QMessageBox.critical(self, "发送失败", f"发给 {name} 的私信未发送成功：\n{error}")

        w = ApiWorker(_send)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)

    def _new_mail(self):
        uid_str, ok = QInputDialog.getText(self, "新建私信", "输入对方用户 ID（整数）：")
        if not ok or not uid_str.strip():
            return
        try:
            uid = int(uid_str.strip())
        except ValueError:
            QMessageBox.warning(self, "输入错误", "用户 ID 必须是整数")
            return
        with QSignalBlocker(self._conv_list):
            self._conv_list.setCurrentRow(-1)
        self._activate_conversation(uid, f"用户 {uid}（新对话）")
        self._conversation_status.setText("新对话，输入消息后发送。")

    def _on_finished(self, _result):
        self._closed = True
        self._mail_gen += 1
        self._conv_gen += 1
