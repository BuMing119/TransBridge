"""
MailsDialog: 私信（Mails）对话框，左侧对话列表 + 右侧消息流。
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QLabel, QTextEdit,
    QPushButton, QMessageBox, QScrollArea, QWidget,
    QInputDialog,
)
from PyQt6.QtCore import Qt

from transbridge.paratranz.api.paratranz_mails_api import ParatranzMailsAPI
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

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._workers: list[ApiWorker] = []
        self._current_uid: int | None = None
        self._current_name: str = ""
        self._my_uid = (ctx.current_user or {}).get("id")
        self._mail_gen = 0
        self._conv_gen = 0
        self.setWindowTitle("私信")
        self.resize(820, 560)
        self._init_ui()
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
        refresh_btn.clicked.connect(self._load_mails)
        new_btn = QPushButton("新建私信")
        new_btn.clicked.connect(self._new_mail)
        toolbar.addWidget(refresh_btn)
        toolbar.addStretch()
        toolbar.addWidget(new_btn)
        left_layout.addLayout(toolbar)
        self._conv_list = QListWidget()
        self._conv_list.itemClicked.connect(self._on_conv_clicked)
        left_layout.addWidget(self._conv_list, stretch=1)
        splitter.addWidget(left)

        # 右侧：对话详情
        right = QWidget()
        right.setMinimumWidth(400)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 0, 0, 0)

        self._conv_label = QLabel("（选择联系人查看对话）")
        self._conv_label.setStyleSheet("font-weight: bold;")
        right_layout.addWidget(self._conv_label)

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
        right_layout.addWidget(self._send_input)

        send_row = QHBoxLayout()
        send_row.addStretch()
        self._send_btn = QPushButton("发送")
        self._send_btn.setEnabled(False)
        self._send_btn.clicked.connect(self._send_message)
        send_row.addWidget(self._send_btn)
        right_layout.addLayout(send_row)

        splitter.addWidget(right)
        splitter.setSizes([240, 560])
        layout.addWidget(splitter)

    def _load_mails(self):
        config = self._ctx.config
        self._mail_gen += 1
        gen = self._mail_gen

        def _fetch():
            api = ParatranzMailsAPI(token=config.token, config=config)
            return _extract_list(api.list_mails(page_size=100))

        def _on_done(mails):
            if self._mail_gen != gen:
                return
            self._on_mails_loaded(mails)

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
        w.error.connect(lambda e: QMessageBox.warning(self, "加载失败", e))
        w.start()
        self._workers.append(w)

    def _on_mails_loaded(self, mails: list):
        # 以对方用户为维度聚合为对话列表
        seen: dict[int, dict] = {}
        my_uid = self._my_uid
        for mail in mails:
            from_id = mail.get("from")
            to_id = mail.get("to")
            other_id = to_id if from_id == my_uid else from_id

            # 尝试从嵌套 user 对象取显示名
            if from_id == my_uid:
                other_user = mail.get("toUser") or mail.get("to_user") or {}
            else:
                other_user = mail.get("fromUser") or mail.get("from_user") or {}
            other_name = _user_name(other_user, other_id)

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

        self._conv_list.clear()
        for info in sorted_convs:
            last = info["last"]
            name = info["name"]
            preview = str(last.get("content", ""))[:30]
            unread = last.get("status") == 0
            item = QListWidgetItem(f"{'[未读] ' if unread else ''}{name}\n{preview}")
            item.setData(Qt.ItemDataRole.UserRole, {"uid": info["uid"], "name": name})
            self._conv_list.addItem(item)

    def _on_conv_clicked(self, item: QListWidgetItem):
        data = item.data(Qt.ItemDataRole.UserRole)
        if data is None:
            return
        uid = data["uid"]
        name = data["name"]
        self._current_uid = uid
        self._current_name = name
        self._conv_label.setText(name)
        self._send_btn.setEnabled(True)
        self._load_conversation(uid)

    def _load_conversation(self, uid: int):
        config = self._ctx.config
        self._conv_gen += 1
        gen = self._conv_gen

        def _fetch():
            api = ParatranzMailsAPI(token=config.token, config=config)
            return api.get_conversation(uid)

        def _on_done(messages):
            if self._conv_gen != gen:
                return
            self._on_conversation_loaded(messages)

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
        w.error.connect(lambda _: None)
        w.start()
        self._workers.append(w)

    def _on_conversation_loaded(self, messages):
        # 清空消息区
        while self._msg_layout.count() > 1:
            it = self._msg_layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

        my_uid = self._my_uid
        # 按时间升序排列（最新消息在底部）
        msgs = sorted(
            messages if isinstance(messages, list) else [],
            key=lambda m: m.get("createdAt", ""),
        )
        for msg in msgs:
            is_mine = msg.get("from") == my_uid
            content = msg.get("html") or msg.get("content") or ""
            created = str(msg.get("createdAt", ""))[:16]

            bubble = QLabel(f"{content}\n<small>{created}</small>")
            bubble.setWordWrap(True)
            bubble.setTextFormat(Qt.TextFormat.RichText)
            bubble.setMaximumWidth(450)

            if is_mine:
                bubble.setStyleSheet(
                    "background:#DCF8C6; padding:6px; border-radius:6px; margin:2px;"
                )
                row = QHBoxLayout()
                row.addStretch()
                row.addWidget(bubble)
            else:
                bubble.setStyleSheet(
                    "background:#FFFFFF; padding:6px; border-radius:6px; margin:2px;"
                )
                row = QHBoxLayout()
                row.addWidget(bubble)
                row.addStretch()

            wrapper = QWidget()
            wrapper.setLayout(row)
            # 插入到 stretch 之前，保持时间顺序从上到下
            self._msg_layout.insertWidget(self._msg_layout.count() - 1, wrapper)

        # 滚动到底部（最新消息）
        self._msg_scroll.verticalScrollBar().setValue(
            self._msg_scroll.verticalScrollBar().maximum()
        )

    def _send_message(self):
        if self._current_uid is None:
            return
        content = self._send_input.toPlainText().strip()
        if not content:
            return
        config = self._ctx.config
        to_uid = self._current_uid

        def _send():
            api = ParatranzMailsAPI(token=config.token, config=config)
            return api.send_mail(to_uid, content)

        def _on_done(_):
            self._send_input.clear()
            self._load_conversation(to_uid)

        w = ApiWorker(_send)
        w.result.connect(_on_done)
        w.error.connect(lambda e: QMessageBox.critical(self, "发送失败", e))
        w.start()
        self._workers.append(w)

    def _new_mail(self):
        uid_str, ok = QInputDialog.getText(
            self, "新建私信", "输入对方用户 ID（整数）："
        )
        if not ok or not uid_str.strip():
            return
        try:
            uid = int(uid_str.strip())
        except ValueError:
            QMessageBox.warning(self, "输入错误", "用户 ID 必须是整数")
            return
        self._current_uid = uid
        self._current_name = str(uid)
        self._conv_label.setText(f"用户 {uid}（新对话）")
        self._send_btn.setEnabled(True)
