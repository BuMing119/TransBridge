"""
ParaTranzWidget: ParaTranz 管理模式整体布局。
左侧项目列表 + 右侧多标签页（概览、文件、词条、术语、成员、历史、贡献、导出、讨论）。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QSplitter, QTabWidget, QVBoxLayout, QWidget

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

        # 右侧：标签页
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

        splitter.addWidget(self._tabs)
        splitter.setSizes([240, 960])

        layout.addWidget(splitter)

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
