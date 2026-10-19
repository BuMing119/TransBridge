"""
ParaTranzWidget: ParaTranz 管理模式整体布局。
左侧项目列表 + 右侧按概览、翻译、协作与记录分组的页面。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QPushButton, QSplitter, QStackedWidget, QTabWidget, QVBoxLayout, QWidget

from transbridge.ui.foundation.accessibility import configure_accessible_widget
from transbridge.ui.foundation.adapters import ThemeSubscription, ThemeView
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle
from transbridge.ui.foundation.theme_service import ThemeSnapshot

from .contribution_tab import ContributionTab
from .export_tab import ExportTab
from .files_tab import FilesTab
from .history_tab import HistoryTab
from .issues_tab import IssuesTab
from .members_tab import MembersTab
from .navigation import ParaTranzNavigation
from .overview_tab import OverviewTab
from .project_panel import ProjectListPanel
from .strings_tab import StringsTab
from .terms_tab import TermsTab


class ParaTranzWidget(QWidget):
    def __init__(self, ctx, parent=None, *, theme_view: ThemeView | None = None):
        super().__init__(parent)
        self._ctx = ctx
        self._theme_view = theme_view
        self._domain_brushes = None if theme_view is None else theme_view.domain_brushes()
        self._theme_subscription: ThemeSubscription | None = None
        configure_accessible_widget(self, name="ParaTranz 管理", description="管理云端项目、词条和协作内容")
        self._init_ui()
        ctx.project_selected.connect(self._sync_project)
        ctx.config_changed.connect(lambda _config: self._sync_project(self._ctx.current_project))
        self._sync_project(ctx.current_project)
        if theme_view is not None:
            self._theme_subscription = theme_view.subscribe(self, self._apply_theme)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        # 左侧：项目列表面板
        self._project_panel = ProjectListPanel(self._ctx)
        splitter.addWidget(self._project_panel)

        # Preserve the original page objects and indices behind grouped navigation.
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        self._navigation = ParaTranzNavigation(right)
        right_layout.addWidget(self._navigation)
        self._tabs = QTabWidget()
        self._tabs.setAccessibleName("ParaTranz 功能分类")
        ComponentStyle.apply_static(self._tabs, ComponentKind.CARD)

        self._overview = OverviewTab(self._ctx)
        self._files = FilesTab(self._ctx)
        self._strings = StringsTab(
            self._ctx,
            theme_view=self._theme_view,
            domain_brushes=self._domain_brushes,
        )
        self._terms = TermsTab(self._ctx)
        self._members = MembersTab(self._ctx)
        self._history = HistoryTab(self._ctx)
        self._contribution = ContributionTab(self._ctx)
        self._export = ExportTab(self._ctx)
        self._issues = IssuesTab(self._ctx)

        self._tabs.addTab(self._overview, "概览")
        self._tabs.addTab(self._files, "文件管理")
        self._tabs.addTab(self._strings, "词条管理")
        self._tabs.addTab(self._terms, "术语管理")
        self._tabs.addTab(self._members, "成员管理")
        self._tabs.addTab(self._history, "历史记录")
        self._tabs.addTab(self._contribution, "贡献统计")
        self._tabs.addTab(self._export, "导出管理")
        self._tabs.addTab(self._issues, "讨论")
        self._tabs.tabBar().hide()
        self._navigation.page_requested.connect(self._tabs.setCurrentIndex)
        self._tabs.currentChanged.connect(self._navigation.select_page)

        self._content = QStackedWidget(right)
        self._empty = QWidget(self._content)
        empty_layout = QVBoxLayout(self._empty)
        empty_layout.addStretch()
        self._empty_message = QLabel(self._empty)
        self._empty_message.setWordWrap(True)
        self._empty_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self._empty_message)
        self._choose_project = QPushButton("选择左侧项目", self._empty)
        self._choose_project.clicked.connect(lambda: self._project_panel._list.setFocus())
        empty_layout.addWidget(self._choose_project, alignment=Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addStretch()
        self._content.addWidget(self._empty)
        self._content.addWidget(self._tabs)
        right_layout.addWidget(self._content, 1)

        splitter.addWidget(right)
        splitter.setSizes([240, 960])

        layout.addWidget(splitter)

    def _sync_project(self, project) -> None:
        connected = bool(self._ctx.config.token)
        selected = project if connected else None
        self._navigation.set_project(selected)
        self._content.setCurrentWidget(self._tabs if selected else self._empty)
        self._choose_project.setVisible(connected)
        self._empty_message.setText(
            "从左侧选择云端项目，开始查看文件、编辑词条或管理协作。\n浏览项目不会改变本地工程的同步目标。"
            if connected
            else "尚未连接 ParaTranz。\n请先在通用设置 → ParaTranz 中配置并验证 Token，再刷新项目列表。"
        )

    def _apply_theme(self, snapshot: ThemeSnapshot) -> None:
        assert self._theme_view is not None
        self._domain_brushes = self._theme_view.domain_brushes(snapshot)
        self._strings.apply_domain_brushes(self._domain_brushes)
        self._tabs.update()

    def closeEvent(self, event) -> None:
        if self._theme_subscription is not None:
            self._theme_subscription.close()
            self._theme_subscription = None
        super().closeEvent(event)

    def refresh_projects(self):
        self._project_panel.load_projects()

    def switch_to_mine(self):
        """切换项目列表到「我参与的」视图。"""
        self._project_panel.switch_to_mine()
