"""
ProjectListPanel: ParaTranz 管理模式左侧项目列表面板。
支持「全部项目」/ 「我参与的」视图切换、关键词搜索、新建项目。
"""

from datetime import datetime

from PyQt6.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTabBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from transbridge.application.projects import ParaTranzProjectBinding
from transbridge.paratranz.api.paratranz_project_api import ParatranzProjectAPI
from transbridge.paratranz.project_catalog import ParaTranzCatalogKey, ParaTranzProjectCatalog
from transbridge.paratranz.service import ParaTranzService
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, SemanticState

from ..workers import ApiWorker


def _extract_list(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "results", "items"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


class NewProjectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建项目")
        self.setMinimumWidth(380)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name = QLineEdit()
        self._name.setPlaceholderText("必填")
        form.addRow("项目名称 *", self._name)

        self._desc = QTextEdit()
        self._desc.setMaximumHeight(70)
        form.addRow("项目说明", self._desc)

        self._source = QLineEdit("en")
        form.addRow("源语言", self._source)

        self._dest = QLineEdit("zh-CN")
        form.addRow("目标语言", self._dest)

        self._game = QLineEdit()
        form.addRow("所属游戏", self._game)

        def _combo(items):
            cb = QComboBox()
            cb.addItems(items)
            return cb

        self._privacy = _combo(["公开 (0)", "内部 (1)", "私密 (2)"])
        form.addRow("隐私设置", self._privacy)
        self._download = _combo(["公开 (0)", "内部 (1)", "私密 (2)"])
        form.addRow("下载权限", self._download)
        self._issue_mode = _combo(["公开 (0)", "内部 (1)", "私密 (2)"])
        form.addRow("讨论权限", self._issue_mode)
        self._review_mode = _combo(["无须 (0)", "一次 (1)", "二次 (2)"])
        form.addRow("校对等级", self._review_mode)
        self._join_mode = _combo(["公开 (0)", "申请 (1)", "测试 (2)", "私密 (3)"])
        form.addRow("加入方式", self._join_mode)

        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("创建项目")
        ComponentStyle.apply_static(ok_btn, ComponentKind.BUTTON)
        ComponentStyle.apply_state(ok_btn, SemanticState.PRIMARY)
        ok_btn.clicked.connect(self._validate)
        cancel_btn = QPushButton("取消")
        ComponentStyle.apply_static(cancel_btn, ComponentKind.BUTTON)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _validate(self):
        if not self._name.text().strip():
            QMessageBox.warning(self, "提示", "项目名称不能为空")
            return
        self.accept()

    def get_data(self) -> dict:
        return {
            "name": self._name.text().strip(),
            "desc": self._desc.toPlainText().strip(),
            "source": self._source.text().strip() or "en",
            "dest": self._dest.text().strip() or "zh-CN",
            "game": self._game.text().strip() or None,
            "privacy": self._privacy.currentIndex(),
            "download": self._download.currentIndex(),
            "issueMode": self._issue_mode.currentIndex(),
            "reviewMode": self._review_mode.currentIndex(),
            "joinMode": self._join_mode.currentIndex(),
        }


class ProjectListPanel(QWidget):
    project_chosen = pyqtSignal(dict)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._all_projects: list[dict] = []
        self._load_status: str | None = None
        self._workers: list[ApiWorker] = []
        self._gen = 0
        self._bind_gen = 0
        self._config_revision = 0
        self._catalog = ParaTranzProjectCatalog()
        self.setMinimumWidth(200)
        self.setMaximumWidth(300)
        self._init_ui()
        ctx.config_changed.connect(self._on_config_changed)
        ctx.project_list_changed.connect(self.load_projects)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # 视图切换
        self._tab_bar = QTabBar()
        self._tab_bar.addTab("全部项目")
        self._tab_bar.addTab("我参与的")
        self._tab_bar.currentChanged.connect(self.load_projects)
        layout.addWidget(self._tab_bar)

        # 搜索
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索项目…")
        self._search.setClearButtonEnabled(True)
        self._search.setAccessibleName("搜索 ParaTranz 项目")
        self._search.textChanged.connect(self._apply_filter)
        layout.addWidget(self._search)

        # 工具栏
        toolbar = QHBoxLayout()
        refresh_btn = QPushButton("刷新")
        refresh_btn.setMinimumHeight(26)
        refresh_btn.clicked.connect(self.load_projects)
        new_btn = QPushButton("新建")
        new_btn.setMinimumHeight(26)
        new_btn.clicked.connect(self._create_project)
        toolbar.addWidget(refresh_btn)
        toolbar.addStretch()
        toolbar.addWidget(new_btn)
        layout.addLayout(toolbar)

        # 项目列表
        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_item_clicked)
        self._list.itemSelectionChanged.connect(self._update_bind_button)
        layout.addWidget(self._list, stretch=1)

        self._bind_btn = QPushButton("设为同步目标")
        self._bind_btn.setMinimumHeight(26)
        self._bind_btn.setEnabled(False)
        self._bind_btn.setAccessibleName("设为当前工程同步目标")
        self._bind_btn.setToolTip("只有点击此按钮才会修改本地工程绑定；浏览项目不会改变同步目标")
        self._bind_btn.clicked.connect(self._bind_selected_project)
        layout.addWidget(self._bind_btn)

        self._status = QLabel("加载中…")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self.load_projects()

    def load_projects(self):
        config = self._ctx.config
        self._gen += 1
        gen = self._gen
        self._all_projects = []
        if not config.token:
            self._load_status = "未配置 Token"
            self._apply_filter()
            return

        show_mine = self._tab_bar.currentIndex() == 1
        uid = config.user_id if show_mine else None
        if show_mine and uid is None:
            self._load_status = "未获取到用户 ID，请重新验证 Token"
            self._apply_filter()
            return

        self._load_status = "加载中…"
        self._apply_filter()

        def _fetch():
            api = ParatranzProjectAPI(token=config.token, config=config)
            projects = []
            page = 1
            while True:
                current = _extract_list(api.list_projects(page=page, page_size=200, uid=uid))
                projects.extend(current)
                if len(current) < 200:
                    break
                page += 1
            return projects

        def _on_done(projects):
            if self._gen != gen:
                return
            self._on_projects_loaded(projects, is_mine=show_mine)

        def _on_error(error):
            if self._gen == gen:
                self._load_status = f"加载失败：{error}"
                self._update_status()

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)

    def _on_projects_loaded(self, projects: list, is_mine: bool = False):
        self._all_projects = projects
        self._load_status = None
        if is_mine:
            self._ctx.mine_project_ids = {p["id"] for p in projects if p.get("id") is not None}
        self._apply_filter()

    def _apply_filter(self):
        keyword = self._search.text().strip().lower()
        current_id = (self._ctx.current_project or {}).get("id")
        with QSignalBlocker(self._list):
            self._list.clear()
            for p in self._all_projects:
                name = p.get("name", "")
                if keyword and keyword not in name.lower():
                    continue
                item = QListWidgetItem(name)
                item.setData(Qt.ItemDataRole.UserRole, p)
                src = p.get("source", "?")
                dest = p.get("dest", "?")
                item.setToolTip(f"{src} → {dest}  |  成员数: {p.get('total', '?')}")
                self._list.addItem(item)
                if current_id is not None and p.get("id") == current_id:
                    self._list.setCurrentItem(item)
        self._update_bind_button()
        self._update_status()

    def _update_bind_button(self):
        self._bind_btn.setEnabled(
            self._list.isEnabled() and bool(self._list.selectedItems()) and self._ctx.active_project_id is not None
        )

    def _update_status(self):
        if not self._list.isEnabled():
            return  # Membership validation owns the status until it finishes.
        total = len(self._all_projects)
        visible = self._list.count()
        if self._load_status:
            message = self._load_status
        elif self._search.text().strip():
            message = f"匹配 {visible} / {total} 个项目" if visible else f"没有匹配的项目（共 {total} 个）"
        else:
            message = f"共 {total} 个项目" if total else "暂无项目"
        self._status.setText(message)

    def _on_item_clicked(self, item: QListWidgetItem | None, _previous=None):
        self._update_bind_button()
        project = None if item is None else item.data(Qt.ItemDataRole.UserRole)
        if project:
            self._ctx.current_project = project
            self.project_chosen.emit(project)

    def _bind_selected_project(self):
        item = self._list.currentItem()
        project = None if item is None else item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(project, dict) or self._ctx.active_project_id is None:
            return
        project_id = project.get("id")
        if isinstance(project_id, bool) or not isinstance(project_id, int):
            QMessageBox.warning(self, "无法绑定", "所选 ParaTranz 项目缺少有效 ID。")
            return
        user_id = None if self._ctx.current_user is None else self._ctx.current_user.get("id")
        if user_id is None:
            user_id = getattr(self._ctx.config, "user_id", None)
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            QMessageBox.warning(self, "无法绑定", "未获取到当前 ParaTranz 账号，请先重新验证 Token。")
            return
        config = self._ctx.config
        if not config.token:
            QMessageBox.warning(self, "无法绑定", "尚未配置 ParaTranz Token。")
            return
        endpoint = str(config.base_url)
        local_project_id = self._ctx.active_project_id
        local_project_revision = self._ctx.project_revision
        config_revision = self._config_revision
        self._bind_gen += 1
        bind_gen = self._bind_gen
        self._bind_btn.setEnabled(False)
        self._list.setEnabled(False)
        self._status.setText("正在验证当前账号的项目成员身份…")

        def _validate_membership():
            service = ParaTranzService.from_config(config)
            try:
                key = ParaTranzCatalogKey(endpoint, user_id, config_revision)
                snapshot = self._catalog.list_my_projects(service, key, refresh=True)
                return next((value for value in snapshot.projects if value.project_id == project_id), None)
            finally:
                service.close()

        def _on_validated(remote_project):
            if bind_gen != self._bind_gen:
                return
            if self._config_revision != config_revision:
                QMessageBox.warning(self, "绑定已取消", "ParaTranz 连接配置在验证期间发生变化，请重新选择。")
                return
            if self._ctx.active_project_id != local_project_id or self._ctx.project_revision != local_project_revision:
                QMessageBox.warning(self, "绑定已取消", "当前本地工程在验证期间发生变化，请重新选择。")
                return
            if remote_project is None:
                QMessageBox.warning(self, "无法绑定", "当前 ParaTranz 账号不是所选项目的成员。")
                return
            now = datetime.now().astimezone().isoformat()
            binding = ParaTranzProjectBinding(
                project_id,
                remote_project.name,
                endpoint,
                user_id,
                now,
                now,
            )
            try:
                result = self._ctx.set_paratranz_binding(binding)
            except RuntimeError as exc:
                QMessageBox.critical(self, "绑定失败", str(exc))
                return
            if not result.is_success:
                message = result.diagnostics[0].message if result.diagnostics else "未知错误"
                QMessageBox.critical(self, "绑定失败", message)
                return
            self._ctx.mine_project_ids = {*getattr(self._ctx, "mine_project_ids", ()), project_id}
            QMessageBox.information(self, "绑定成功", "已将所选项目设为当前本地工程的默认同步目标。")

        def _on_validation_error(error: str):
            if bind_gen == self._bind_gen:
                QMessageBox.warning(self, "无法验证项目成员身份", f"ParaTranz 验证失败：{error}")

        worker = ApiWorker(_validate_membership, route_http_errors=False)
        worker.result.connect(_on_validated)
        worker.error.connect(_on_validation_error)
        worker.finished.connect(lambda: self._binding_worker_finished(worker, bind_gen))
        self._workers.append(worker)
        worker.start()

    def _binding_worker_finished(self, worker: ApiWorker, bind_gen: int) -> None:
        if worker in self._workers:
            self._workers.remove(worker)
        worker.deleteLater()
        if bind_gen != self._bind_gen:
            return
        self._list.setEnabled(True)
        self._update_bind_button()
        self._update_status()

    def _create_project(self):
        dlg = NewProjectDialog(self)
        if not dlg.exec():
            return
        data = dlg.get_data()
        config = self._ctx.config

        def _create():
            api = ParatranzProjectAPI(token=config.token, config=config)
            return api.create_project(data)

        def _on_done(p):
            name = p.get("name", data.get("name", "")) if isinstance(p, dict) else data.get("name", "")
            QMessageBox.information(self, "成功", f"项目「{name}」已创建")
            self._ctx.project_list_changed.emit()

        w = ApiWorker(_create)
        w.result.connect(_on_done)
        w.error.connect(lambda e: QMessageBox.critical(self, "创建失败", e))
        w.start()
        self._workers.append(w)

    def switch_to_mine(self):
        """切换到「我参与的」视图并刷新列表。"""
        if self._tab_bar.currentIndex() == 1:
            self.load_projects()  # 已在该标签，currentChanged 不会触发，手动刷新
        else:
            self._tab_bar.setCurrentIndex(1)  # 触发 currentChanged -> load_projects

    def _on_config_changed(self, _):
        self._config_revision += 1
        self._bind_gen += 1
        self._catalog.clear()
        self._list.setEnabled(True)
        self._bind_btn.setEnabled(False)
        self.load_projects()
