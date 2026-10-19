"""
IssuesTab: 讨论（Issues）标签页，左侧列表 + 右侧详情。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from transbridge.paratranz.api.paratranz_issues_api import ParatranzIssuesAPI
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
        self._drafts: dict[tuple[int, int], str] = {}
        self._busy_issues: set[tuple[int, int]] = set()
        self._init_ui()
        ctx.project_selected.connect(self._on_project_changed)
        ctx.paratranz_permissions_changed.connect(self._refresh_permissions)

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
        for button in (refresh_btn, self._new_btn):
            ComponentStyle.apply_static(button, ComponentKind.BUTTON)
        toolbar.addWidget(refresh_btn)
        toolbar.addStretch()
        toolbar.addWidget(self._new_btn)
        left_layout.addLayout(toolbar)

        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_issue_clicked)
        left_layout.addWidget(self._list, stretch=1)
        self._list_status = QLabel("选择项目查看讨论")
        self._list_status.setTextFormat(Qt.TextFormat.PlainText)
        self._list_status.setWordWrap(True)
        left_layout.addWidget(self._list_status)

        splitter.addWidget(left)

        # 右侧：详情
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 0, 0, 0)

        self._title_lbl = ElidedLabel("（选择一个讨论查看详情）")
        title_font = QFont(self._title_lbl.font())
        title_font.setPointSizeF(max(11.0, title_font.pointSizeF() + 1.0))
        title_font.setWeight(QFont.Weight.DemiBold)
        self._title_lbl.setFont(title_font)
        self._title_lbl.setFixedHeight(self._title_lbl.fontMetrics().height() + 4)
        self._title_lbl.setAccessibleName("讨论标题")
        ComponentStyle.apply_static(self._title_lbl, ComponentKind.LABEL)
        right_layout.addWidget(self._title_lbl)

        self._content_view = QTextEdit()
        self._content_view.setReadOnly(True)
        self._content_view.setMaximumHeight(120)
        right_layout.addWidget(self._content_view)

        right_layout.addWidget(QLabel("回复列表："))
        self._replies_view = QTextEdit()
        self._replies_view.setReadOnly(True)
        right_layout.addWidget(self._replies_view, stretch=1)
        self._detail_retry = QPushButton("重试加载回复")
        ComponentStyle.apply_static(self._detail_retry, ComponentKind.BUTTON)
        self._detail_retry.clicked.connect(self._load_issue_detail)
        self._detail_retry.hide()
        right_layout.addWidget(self._detail_retry, alignment=Qt.AlignmentFlag.AlignRight)

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
        for button in (self._reply_btn, self._close_btn):
            ComponentStyle.apply_static(button, ComponentKind.BUTTON)
        ComponentStyle.apply_state(self._reply_btn, SemanticState.PRIMARY)
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

    def _set_title(self, title: str) -> None:
        self._title_lbl.set_full_text(title)
        self._title_lbl.setToolTip(title)
        self._title_lbl.setAccessibleDescription(title)

    def _on_status_changed(self, idx: int):
        self._status_filter = idx
        self.load_issues()

    def _on_project_changed(self, project):
        self._gen += 1
        self._clear_detail()
        self._project_id = project.get("id") if project else None
        self._list.clear()
        self._list_status.setText("选择项目查看讨论" if not project else "")
        self._access_ok = True

        if project and self._project_id:
            issue_mode = project.get("issueMode", 0)
            is_admin = self._ctx.is_admin()
            is_member = self._ctx.is_member()

            if issue_mode == 2 and not is_admin:
                self._access_ok = False
                self._set_title("⚠ 讨论权限为私密，仅管理员可见")
            elif issue_mode == 1 and not is_member:
                self._access_ok = False
                self._set_title("⚠ 讨论权限为内部，仅项目成员可见")

            self._new_btn.setEnabled(self._access_ok)
            if self._access_ok:
                self.load_issues()
        else:
            self._new_btn.setEnabled(False)

    def _refresh_permissions(self):
        project = self._ctx.current_project
        if not project or project.get("id") != self._project_id:
            return
        mode = project.get("issueMode", 0)
        allowed = mode == 0 or (self._ctx.is_member() if mode == 1 else self._ctx.is_admin())
        if allowed != self._access_ok:
            self._on_project_changed(project)

    def load_issues(self):
        if not self._project_id or not self._access_ok:
            return
        config = self._ctx.config
        pid = self._project_id
        status = self._status_filter
        self._gen += 1
        gen = self._gen
        self._clear_detail()
        self._list.clear()
        self._list_status.setText("正在加载讨论…")

        def _fetch():
            api = ParatranzIssuesAPI(token=config.token, config=config)
            return _extract_list(api.list_issues(pid, status=status))

        def _on_done(issues):
            if self._gen != gen:
                return
            self._on_issues_loaded(issues)

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
        w.error.connect(
            lambda e: self._list_status.setText(f"加载失败：{e}\n请点击刷新重试。") if self._gen == gen else None
        )
        w.start()
        self._workers.append(w)

    def _on_issues_loaded(self, issues: list):
        self._list.clear()
        self._list_status.setText(f"共 {len(issues)} 个讨论" if issues else "当前分类暂无讨论")
        for issue in issues:
            title = issue.get("title", "（无标题）")
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, issue)
            self._list.addItem(item)

    def _issue_key(self) -> tuple[int, int] | None:
        if self._project_id is None or not self._current_issue:
            return None
        return self._project_id, self._current_issue.get("id")

    def _clear_detail(self):
        key = self._issue_key()
        if key is not None:
            self._drafts[key] = self._reply_input.toPlainText()
        self._detail_gen += 1
        self._current_issue = None
        self._set_title("（选择一个讨论查看详情）")
        self._content_view.clear()
        self._replies_view.clear()
        self._reply_input.clear()
        self._detail_retry.hide()
        self._set_detail_enabled(False)

    def _on_issue_clicked(self, item: QListWidgetItem | None, _previous=None):
        self._clear_detail()
        if item is None:
            return
        issue = item.data(Qt.ItemDataRole.UserRole)
        if not issue:
            return
        self._current_issue = issue
        self._set_title(str(issue.get("title", "—")))
        self._content_view.setPlainText(issue.get("content", "") or "")
        key = self._issue_key()
        self._reply_input.setPlainText(self._drafts.get(key, ""))
        self._set_detail_enabled(key not in self._busy_issues)
        is_closed = issue.get("status", 0) == 1
        self._close_btn.setText("重开讨论" if is_closed else "关闭讨论")
        self._load_issue_detail()

    def _load_issue_detail(self):
        if not self._current_issue or not self._project_id or not self._access_ok:
            return
        config = self._ctx.config
        pid = self._project_id
        iid = self._current_issue.get("id")
        self._detail_gen += 1
        detail_gen = self._detail_gen
        self._replies_view.setPlainText("正在加载回复…")
        self._detail_retry.hide()

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

        def _on_error(error):
            if self._detail_gen == detail_gen:
                self._replies_view.setPlainText(f"加载回复失败：{error}")
                self._detail_retry.show()

        w = ApiWorker(_fetch)
        w.result.connect(_on_detail)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)

    def _send_reply(self):
        key = self._issue_key()
        if key is None or not self._access_ok or key in self._busy_issues:
            return
        draft = self._reply_input.toPlainText()
        content = draft.strip()
        if not content:
            return
        config = self._ctx.config
        pid = self._project_id
        iid = self._current_issue.get("id")
        self._drafts[key] = draft
        self._busy_issues.add(key)
        self._set_detail_enabled(False)

        def _reply():
            api = ParatranzIssuesAPI(token=config.token, config=config)
            return api.reply_issue(pid, iid, content)

        def _on_done(_):
            self._finish_issue_operation(key)
            if self._drafts.get(key) == draft:
                self._drafts.pop(key, None)
            if self._issue_key() == key:
                if self._reply_input.toPlainText() == draft:
                    self._reply_input.clear()
                self._load_issue_detail()

        def _on_error(error):
            self._finish_issue_operation(key)
            QMessageBox.critical(self, "回复失败", f"项目 {pid} 的讨论 {iid} 回复失败：\n{error}")

        w = ApiWorker(_reply)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)

    def _toggle_close(self):
        key = self._issue_key()
        if key is None or not self._access_ok or key in self._busy_issues:
            return
        issue = self._current_issue
        is_closed = issue.get("status", 0) == 1
        new_status = 0 if is_closed else 1
        config = self._ctx.config
        pid = self._project_id
        iid = self._current_issue.get("id")
        self._busy_issues.add(key)
        self._set_detail_enabled(False)

        def _toggle():
            api = ParatranzIssuesAPI(token=config.token, config=config)
            return api.update_issue(pid, iid, {"status": new_status})

        def _on_done(_):
            self._finish_issue_operation(key)
            issue["status"] = new_status
            if self._issue_key() == key:
                self.load_issues()

        def _on_error(error):
            self._finish_issue_operation(key)
            QMessageBox.critical(self, "操作失败", f"项目 {pid} 的讨论 {iid} 状态更新失败：\n{error}")

        w = ApiWorker(_toggle)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)

    def _finish_issue_operation(self, key):
        self._busy_issues.discard(key)
        if self._issue_key() == key:
            self._set_detail_enabled(self._access_ok)

    def _create_issue(self):
        pid = self._project_id
        if not pid or not self._access_ok:
            return
        dlg = NewIssueDialog(self)
        if not dlg.exec() or self._project_id != pid or not self._access_ok:
            return
        data = dlg.get_data()
        config = self._ctx.config

        def _create():
            api = ParatranzIssuesAPI(token=config.token, config=config)
            return api.create_issue(pid, data["title"], data["content"])

        w = ApiWorker(_create)
        w.result.connect(lambda _: self.load_issues() if self._project_id == pid else None)
        w.error.connect(lambda e: QMessageBox.critical(self, "创建失败", e))
        w.start()
        self._workers.append(w)
