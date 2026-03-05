from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QStatusBar, QLabel, QMessageBox,
)
from PyQt6.QtCore import Qt

from .context import AppContext
from .workers import ApiWorker
from .workbench.widget import WorkbenchWidget
from .paratranz.widget import ParaTranzWidget
from .paratranz.config_dialog import ConfigDialog
from src.transbridge.paratranz.api.paratranz_user_api import ParatranzUserAPI


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("TransBridge")
        self.resize(1280, 820)

        self._ctx = AppContext(self)
        self._workers: list[ApiWorker] = []

        self._init_menu()
        self._init_central()
        self._init_status_bar()

        self._ctx.user_changed.connect(self._on_user_changed)
        self._ctx.project_selected.connect(self._on_project_selected)
        self._ctx.collection_changed.connect(self._on_collection_changed)

        if self._ctx.config.token:
            self._load_current_user()
        else:
            self._show_config_dialog()

    # ── Menu ──────────────────────────────────────────────────

    def _init_menu(self):
        mb = self.menuBar()

        app_menu = mb.addMenu("TransBridge")
        app_menu.addAction("关于…").triggered.connect(self._show_about)

        file_menu = mb.addMenu("文件")
        refresh_act = file_menu.addAction("刷新项目列表")
        refresh_act.setShortcut("Ctrl+R")
        refresh_act.triggered.connect(self._refresh_projects)
        file_menu.addSeparator()
        file_menu.addAction("设置 / API 配置").triggered.connect(self._show_config_dialog)
        file_menu.addSeparator()
        quit_act = file_menu.addAction("退出")
        quit_act.setShortcut("Ctrl+Q")
        quit_act.triggered.connect(self.close)

        acct_menu = mb.addMenu("账户")
        acct_menu.addAction("我的信息").triggered.connect(self._show_user_dialog)
        acct_menu.addAction("私信").triggered.connect(self._show_mails_dialog)

        help_menu = mb.addMenu("帮助")
        help_menu.addAction("关于").triggered.connect(self._show_about)

    # ── Central widget ────────────────────────────────────────

    def _init_central(self):
        self._mode_tabs = QTabWidget()
        self._mode_tabs.setTabPosition(QTabWidget.TabPosition.North)

        self._workbench = WorkbenchWidget(self._ctx)
        self._pt_widget = ParaTranzWidget(self._ctx)

        self._mode_tabs.addTab(self._workbench, "工作台")
        self._mode_tabs.addTab(self._pt_widget, "ParaTranz 管理")

        self.setCentralWidget(self._mode_tabs)

    # ── Status bar ────────────────────────────────────────────

    def _init_status_bar(self):
        sb = QStatusBar()
        self.setStatusBar(sb)

        self._user_label = QLabel("未登录")
        self._project_label = QLabel("未选择项目")
        self._msg_label = QLabel("就绪")

        sb.addPermanentWidget(self._user_label)
        sb.addPermanentWidget(QLabel(" | "))
        sb.addPermanentWidget(self._project_label)
        sb.addPermanentWidget(QLabel(" | "))
        sb.addWidget(self._msg_label)

    # ── Context signal handlers ───────────────────────────────

    def _on_user_changed(self, user):
        if user:
            name = user.get("nickname") or user.get("username") or "已登录"
            self._user_label.setText(f"用户: {name}")
        else:
            self._user_label.setText("未登录")

    def _on_project_selected(self, project):
        if project:
            self._project_label.setText(f"项目: {project.get('name', '')}")
        else:
            self._project_label.setText("未选择项目")

    def _on_collection_changed(self, collection):
        if collection:
            self.show_message(f"集合已加载，共 {len(collection)} 条词条")

    def show_message(self, msg: str):
        self._msg_label.setText(msg)

    # ── Actions ───────────────────────────────────────────────

    def _load_current_user(self):
        """使用配置文件中的 user_id 加载当前用户信息。"""
        config = self._ctx.config
        if not config.user_id:
            self._ctx.current_user = {"nickname": "已登录", "id": None}
            self.show_message("提示：在「设置 / API 配置」中填写用户 ID 以显示完整用户信息")
            return

        uid = config.user_id

        def _fetch():
            api = ParatranzUserAPI(token=config.token, config=config)
            return api.get_user(uid)

        w = ApiWorker(_fetch)
        w.result.connect(lambda u: setattr(self._ctx, "current_user", u))
        w.error.connect(lambda e: self.show_message(f"获取用户信息失败: {e}"))
        w.start()
        self._workers.append(w)

    def _refresh_projects(self):
        self._pt_widget.refresh_projects()

    def _show_config_dialog(self):
        dlg = ConfigDialog(self._ctx, self)
        dlg.exec()
        if self._ctx.config.token and not self._ctx.current_user:
            self._load_current_user()

    def _show_user_dialog(self):
        if not self._ctx.current_user:
            self.show_message("请先配置 API Token")
            return
        from .paratranz.user_dialog import UserInfoDialog
        UserInfoDialog(self._ctx, self).exec()

    def _show_mails_dialog(self):
        if not self._ctx.current_user:
            self.show_message("请先配置 API Token")
            return
        from .paratranz.mails_dialog import MailsDialog
        MailsDialog(self._ctx, self).exec()

    def _show_about(self):
        QMessageBox.about(
            self, "关于 TransBridge",
            "TransBridge\n\nESP 插件翻译辅助工具，对接 ParaTranz 平台。",
        )
