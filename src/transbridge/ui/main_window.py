from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QStatusBar, QLabel, QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer

from transbridge import __version__
from .context import AppContext
from .workers import ApiWorker, get_http_error_bus, get_api_status_bus
from .workbench.widget import WorkbenchWidget
from .paratranz.widget import ParaTranzWidget
from .paratranz.config_dialog import ConfigDialog
from src.transbridge.paratranz.api.paratranz_user_api import ParatranzUserAPI


class _ApiStatusIndicator(QLabel):
    """状态栏 API 状态指示器：绿点（正常）/ 转圈动画（请求中）/ 红点（异常）。"""

    _SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = 0       # 当前进行中的请求数
        self._last_ok = True   # 上一批请求是否全部成功
        self._spin_idx = 0

        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._tick)

        self._refresh()

    # ── 公共槽 ────────────────────────────────────────────────

    def on_request_started(self):
        if self._active == 0:
            self._last_ok = True   # 新一批请求开始，乐观重置
        self._active += 1
        if not self._timer.isActive():
            self._timer.start()
        self._refresh()

    def on_request_finished(self, success: bool):
        self._active = max(0, self._active - 1)
        if not success:
            self._last_ok = False
        if self._active == 0:
            self._timer.stop()
        self._refresh()

    # ── 内部 ──────────────────────────────────────────────────

    def _tick(self):
        self._spin_idx = (self._spin_idx + 1) % len(self._SPINNER)
        self._refresh()

    def _refresh(self):
        if self._active > 0:
            self.setText(
                f'<span style="color:#888">{self._SPINNER[self._spin_idx]} 请求中</span>'
            )
        elif self._last_ok:
            self.setText('<span style="color:green">● 正常</span>')
        else:
            self.setText('<span style="color:red">● 异常</span>')


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
        self._ctx.navigate_to.connect(self._on_navigate_to)

        get_http_error_bus().http_error.connect(self._on_http_error)

        if self._ctx.config.token:
            self._load_current_user()
        else:
            self._show_config_dialog()

    # ── Menu ──────────────────────────────────────────────────

    def _init_menu(self):
        mb = self.menuBar()

        tools_menu = mb.addMenu("小工具")
        self._ai_translator_act = tools_menu.addAction("🤖 AI 自动翻译")
        self._ai_translator_act.triggered.connect(self._open_ai_translator)

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
        self._api_indicator = _ApiStatusIndicator()
        self._msg_label = QLabel("就绪")

        sb.addPermanentWidget(self._user_label)
        sb.addPermanentWidget(QLabel(" | "))
        sb.addPermanentWidget(self._project_label)
        sb.addPermanentWidget(QLabel(" | "))
        sb.addPermanentWidget(self._api_indicator)
        sb.addPermanentWidget(QLabel(" | "))
        sb.addWidget(self._msg_label)

        # 连接全局 API 状态总线
        bus = get_api_status_bus()
        bus.request_started.connect(self._api_indicator.on_request_started)
        bus.request_finished.connect(self._api_indicator.on_request_finished)

    # ── Context signal handlers ───────────────────────────────

    def _on_http_error(self, status: int, message: str):
        """集中处理 401 / 403 HTTP 错误，替代各标签页各自弹 QMessageBox 的行为。"""
        if status == 401:
            self.show_message("Token 已失效，请重新配置")
            self._show_config_dialog()
        elif status == 403:
            self.show_message("权限不足，无法执行此操作")

    def _on_navigate_to(self, index: int):
        self._mode_tabs.setCurrentIndex(index)
        if index == 1:
            self._pt_widget.switch_to_mine()

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
        """通过 GET /users/my 加载当前用户信息，同时缓存 user_id 到配置。"""
        config = self._ctx.config

        def _fetch():
            api = ParatranzUserAPI(token=config.token, config=config)
            return api.get_my_user()

        def _on_done(u):
            self._ctx.current_user = u
            # 自动缓存 uid（若配置中尚未记录）
            uid = u.get("id") if isinstance(u, dict) else None
            if uid and config.user_id != uid:
                config.user_id = uid
                config.save_to_file()

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
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

    def _open_ai_translator(self):
        self._workbench.open_tool("ai_translator")

    def _show_about(self):
        QMessageBox.about(
            self, "关于 TransBridge",
            f"TransBridge v{__version__}\n\nESP 插件翻译辅助工具，对接 ParaTranz 平台。",
        )
