"""
ParaTranzWidget: ParaTranz 管理模式整体布局。
左侧项目列表 + 右侧多标签页（概览、文件、词条、术语、成员、历史、贡献、导出、讨论）。
"""

from PyQt6.QtWidgets import QWidget, QSplitter, QTabWidget, QVBoxLayout
from PyQt6.QtCore import Qt

from .project_panel import ProjectListPanel
from .overview_tab import OverviewTab
from .files_tab import FilesTab
from .strings_tab import StringsTab
from .terms_tab import TermsTab
from .members_tab import MembersTab
from .history_tab import HistoryTab
from .contribution_tab import ContributionTab
from .export_tab import ExportTab
from .issues_tab import IssuesTab


class ParaTranzWidget(QWidget):

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._init_ui()

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

        self._overview = OverviewTab(self._ctx)
        self._files = FilesTab(self._ctx)
        self._strings = StringsTab(self._ctx)
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

    def refresh_projects(self):
        self._project_panel.load_projects()

    def switch_to_mine(self):
        """切换项目列表到「我参与的」视图。"""
        self._project_panel.switch_to_mine()
