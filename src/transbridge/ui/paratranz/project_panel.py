"""
ProjectListPanel: ParaTranz 管理模式左侧项目列表面板。
支持「全部项目」/ 「我参与的」视图切换、关键词搜索、新建项目。
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabBar, QLineEdit,
    QPushButton, QListWidget, QListWidgetItem, QDialog,
    QFormLayout, QLabel, QComboBox,
    QTextEdit, QMessageBox,
)
from PyQt6.QtCore import pyqtSignal, Qt

from transbridge.paratranz.api.paratranz_project_api import ParatranzProjectAPI
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
        ok_btn = QPushButton("创建")
        ok_btn.clicked.connect(self._validate)
        cancel_btn = QPushButton("取消")
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
        self._workers: list[ApiWorker] = []
        self._gen = 0
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
        self._search.textChanged.connect(self._apply_filter)
        layout.addWidget(self._search)

        # 工具栏
        toolbar = QHBoxLayout()
        refresh_btn = QPushButton("刷新")
        refresh_btn.setFixedHeight(26)
        refresh_btn.clicked.connect(self.load_projects)
        new_btn = QPushButton("新建")
        new_btn.setFixedHeight(26)
        new_btn.clicked.connect(self._create_project)
        toolbar.addWidget(refresh_btn)
        toolbar.addStretch()
        toolbar.addWidget(new_btn)
        layout.addLayout(toolbar)

        # 项目列表
        self._list = QListWidget()
        self._list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._list, stretch=1)

        self._status = QLabel("加载中…")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status)

        if self._ctx.config.token:
            self.load_projects()

    def load_projects(self):
        config = self._ctx.config
        if not config.token:
            self._status.setText("未配置 Token")
            return

        show_mine = self._tab_bar.currentIndex() == 1
        uid = config.user_id if show_mine else None
        if show_mine and uid is None:
            self._status.setText("未获取到用户 ID，请重新验证 Token")
            return

        self._status.setText("加载中…")
        self._list.clear()
        self._gen += 1
        gen = self._gen

        def _fetch():
            api = ParatranzProjectAPI(token=config.token, config=config)
            return _extract_list(api.list_projects(page=1, page_size=200, uid=uid))

        def _on_done(projects):
            if self._gen != gen:
                return
            self._on_projects_loaded(projects, is_mine=show_mine)

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
        w.error.connect(lambda e: self._status.setText(f"加载失败：{e}"))
        w.start()
        self._workers.append(w)

    def _on_projects_loaded(self, projects: list, is_mine: bool = False):
        self._all_projects = projects
        if is_mine:
            self._ctx.mine_project_ids = {p["id"] for p in projects if p.get("id") is not None}
        self._apply_filter()
        self._status.setText(f"共 {len(projects)} 个项目")

    def _apply_filter(self):
        keyword = self._search.text().strip().lower()
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

    def _on_item_clicked(self, item: QListWidgetItem):
        project = item.data(Qt.ItemDataRole.UserRole)
        if project:
            self._ctx.current_project = project
            self.project_chosen.emit(project)

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
            name = p.get('name', data.get('name', '')) if isinstance(p, dict) else data.get('name', '')
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
        self.load_projects()
