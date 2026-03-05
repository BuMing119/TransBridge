"""
IssuesTab: 讨论（Issues）标签页，左侧列表 + 右侧详情。
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QGroupBox, QLabel,
    QPushButton, QTextEdit, QTabBar, QMessageBox,
    QDialog, QFormLayout, QLineEdit,
)
from PyQt6.QtCore import Qt

from src.transbridge.paratranz.api.paratranz_issues_api import ParatranzIssuesAPI
from ..workers import ApiWorker


def _extract_list(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("data", "results", "items"):
            if isinstance(data.get(k), list):
                return data[k]
    return []


class NewIssueDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建讨论")
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._title_input = QLineEdit()
        self._title_input.setPlaceholderText("必填")
        form.addRow("标题 *", self._title_input)
        self._content_input = QTextEdit()
        self._content_input.setPlaceholderText("支持 Markdown")
        self._content_input.setMinimumHeight(100)
        form.addRow("内容", self._content_input)
        layout.addLayout(form)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("发起")
        ok_btn.clicked.connect(self._validate)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _validate(self):
        if not self._title_input.text().strip():
            QMessageBox.warning(self, "提示", "标题不能为空")
            return
        self.accept()

    def get_data(self):
        return {
            "title": self._title_input.text().strip(),
            "content": self._content_input.toPlainText().strip(),
        }


class IssuesTab(QWidget):

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._workers: list[ApiWorker] = []
        self._project_id: int | None = None
        self._current_issue: dict | None = None
        self._status_filter = 0  # 0=open, 1=closed
        self._gen = 0
        self._detail_gen = 0
        self._access_ok = True
        self._init_ui()
        ctx.project_selected.connect(self._on_project_changed)

    def _init_ui(self):
        layout = QVBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：列表
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # 状态切换
        tab_row = QHBoxLayout()
        self._tab_bar = QTabBar()
        self._tab_bar.addTab("讨论中")
        self._tab_bar.addTab("已关闭")
        self._tab_bar.currentChanged.connect(self._on_status_changed)
        tab_row.addWidget(self._tab_bar, stretch=1)
        left_layout.addLayout(tab_row)

        toolbar = QHBoxLayout()
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.load_issues)
        self._new_btn = QPushButton("新建讨论")
        self._new_btn.clicked.connect(self._create_issue)
        toolbar.addWidget(refresh_btn)
        toolbar.addStretch()
        toolbar.addWidget(self._new_btn)
        left_layout.addLayout(toolbar)

        self._list = QListWidget()
        self._list.itemClicked.connect(self._on_issue_clicked)
        left_layout.addWidget(self._list, stretch=1)

        splitter.addWidget(left)

        # 右侧：详情
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 0, 0, 0)

        self._title_lbl = QLabel("（选择一个讨论查看详情）")
        self._title_lbl.setWordWrap(True)
        self._title_lbl.setStyleSheet("font-size: 14px; font-weight: bold;")
        right_layout.addWidget(self._title_lbl)

        self._content_view = QTextEdit()
        self._content_view.setReadOnly(True)
        self._content_view.setMaximumHeight(120)
        right_layout.addWidget(self._content_view)

        right_layout.addWidget(QLabel("回复列表："))
        self._replies_view = QTextEdit()
        self._replies_view.setReadOnly(True)
        right_layout.addWidget(self._replies_view, stretch=1)

        right_layout.addWidget(QLabel("发送回复："))
        self._reply_input = QTextEdit()
        self._reply_input.setMaximumHeight(70)
        self._reply_input.setPlaceholderText("支持 Markdown")
        right_layout.addWidget(self._reply_input)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._reply_btn = QPushButton("发送回复")
        self._reply_btn.clicked.connect(self._send_reply)
        self._close_btn = QPushButton("关闭讨论")
        self._close_btn.clicked.connect(self._toggle_close)
        btn_row.addWidget(self._reply_btn)
        btn_row.addWidget(self._close_btn)
        right_layout.addLayout(btn_row)

        splitter.addWidget(right)
        splitter.setSizes([280, 700])
        layout.addWidget(splitter)

        self._set_detail_enabled(False)

    def _set_detail_enabled(self, enabled: bool):
        for w in (self._reply_input, self._reply_btn, self._close_btn):
            w.setEnabled(enabled)

    def _on_status_changed(self, idx: int):
        self._status_filter = idx
        self.load_issues()

    def _on_project_changed(self, project):
        self._project_id = project.get("id") if project else None
        self._list.clear()
        self._title_lbl.setText("（选择一个讨论查看详情）")
        self._content_view.clear()
        self._replies_view.clear()
        self._set_detail_enabled(False)
        self._access_ok = True

        if project and self._project_id:
            issue_mode = project.get("issueMode", 0)
            is_admin = self._ctx.is_admin()
            is_member = self._ctx.is_member()

            if issue_mode == 2 and not is_admin:
                self._access_ok = False
                self._title_lbl.setText("⚠ 讨论权限为私密，仅管理员可见")
            elif issue_mode == 1 and not is_member:
                self._access_ok = False
                self._title_lbl.setText("⚠ 讨论权限为内部，仅项目成员可见")

            self._new_btn.setEnabled(self._access_ok)
            if self._access_ok:
                self.load_issues()
        else:
            self._new_btn.setEnabled(False)

    def load_issues(self):
        if not self._project_id or not self._access_ok:
            return
        config = self._ctx.config
        pid = self._project_id
        status = self._status_filter
        self._gen += 1
        gen = self._gen

        def _fetch():
            api = ParatranzIssuesAPI(token=config.token, config=config)
            return _extract_list(api.list_issues(pid, status=status))

        def _on_done(issues):
            if self._gen != gen:
                return
            self._on_issues_loaded(issues)

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
        w.error.connect(lambda e: QMessageBox.warning(self, "加载失败", e))
        w.start()
        self._workers.append(w)

    def _on_issues_loaded(self, issues: list):
        self._list.clear()
        for issue in issues:
            title = issue.get("title", "（无标题）")
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, issue)
            self._list.addItem(item)

    def _on_issue_clicked(self, item: QListWidgetItem):
        issue = item.data(Qt.ItemDataRole.UserRole)
        if not issue:
            return
        self._current_issue = issue
        self._title_lbl.setText(issue.get("title", "—"))
        self._content_view.setPlainText(issue.get("content", "") or "")
        self._replies_view.clear()
        self._set_detail_enabled(True)
        is_closed = issue.get("status", 0) == 1
        self._close_btn.setText("重开讨论" if is_closed else "关闭讨论")

        # 加载详情（含回复）
        config = self._ctx.config
        pid = self._project_id
        iid = issue.get("id")
        self._detail_gen += 1
        detail_gen = self._detail_gen

        def _fetch():
            api = ParatranzIssuesAPI(token=config.token, config=config)
            return api.get_issue(pid, iid)

        def _on_detail(detail):
            if self._detail_gen != detail_gen:
                return
            activities = detail.get("activities") or []
            text = ""
            for act in activities:
                user = act.get("user") or {}
                name = user.get("nickname", "—") if isinstance(user, dict) else "—"
                content = act.get("content", "")
                created = str(act.get("createdAt", ""))[:10]
                text += f"[{created}] {name}:\n{content}\n\n"
            self._replies_view.setPlainText(text or "（暂无回复）")

        w = ApiWorker(_fetch)
        w.result.connect(_on_detail)
        w.error.connect(lambda _: None)
        w.start()
        self._workers.append(w)

    def _send_reply(self):
        if not self._current_issue:
            return
        content = self._reply_input.toPlainText().strip()
        if not content:
            return
        config = self._ctx.config
        pid = self._project_id
        iid = self._current_issue.get("id")

        def _reply():
            api = ParatranzIssuesAPI(token=config.token, config=config)
            return api.reply_issue(pid, iid, content)

        def _on_done(_):
            self._reply_input.clear()
            # 重新加载详情
            self._on_issue_clicked(
                self._list.selectedItems()[0] if self._list.selectedItems() else None
            )

        w = ApiWorker(_reply)
        w.result.connect(_on_done)
        w.error.connect(lambda e: QMessageBox.critical(self, "回复失败", e))
        w.start()
        self._workers.append(w)

    def _toggle_close(self):
        if not self._current_issue:
            return
        is_closed = self._current_issue.get("status", 0) == 1
        new_status = 0 if is_closed else 1
        config = self._ctx.config
        pid = self._project_id
        iid = self._current_issue.get("id")

        def _toggle():
            api = ParatranzIssuesAPI(token=config.token, config=config)
            return api.update_issue(pid, iid, {"status": new_status})

        def _on_done(_):
            self._current_issue["status"] = new_status
            self._close_btn.setText("重开讨论" if new_status == 1 else "关闭讨论")
            self.load_issues()

        w = ApiWorker(_toggle)
        w.result.connect(_on_done)
        w.error.connect(lambda e: QMessageBox.critical(self, "操作失败", e))
        w.start()
        self._workers.append(w)

    def _create_issue(self):
        dlg = NewIssueDialog(self)
        if not dlg.exec():
            return
        data = dlg.get_data()
        config = self._ctx.config
        pid = self._project_id

        def _create():
            api = ParatranzIssuesAPI(token=config.token, config=config)
            return api.create_issue(pid, data["title"], data["content"])

        w = ApiWorker(_create)
        w.result.connect(lambda _: self.load_issues())
        w.error.connect(lambda e: QMessageBox.critical(self, "创建失败", e))
        w.start()
        self._workers.append(w)
