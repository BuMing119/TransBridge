from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QTabWidget, QSplitter, QMessageBox, QMenuBar, QStatusBar
)
from PyQt6.QtCore import pyqtSignal, Qt

from .client_ui import ParatranzClientUI
from .project_ui import ProjectUI
from .files_ui import FilesUI
from .strings_ui import StringsUI
from .terms_ui import TermsUI
from .export_ui import ExportUI
from .members_ui import MembersUI
from .history_ui import HistoryUI
from .contribution_ui import ContributionUI
from .user_ui import UserUI
from src.transbridge.paratranz.config_manager import ParatranzConfig


class ParatranzMainWindow(QMainWindow):
    """
    Paratranz 主窗口
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("TransBridge - Paratranz 翻译管理工具")
        self.resize(1200, 800)

        # 客户端配置
        self.client_config = ParatranzConfig.create_or_load()

        self.init_ui()
        self.init_menu()
        self.init_status_bar()

        # 如果没有有效的令牌，首先显示配置对话框
        if not self.client_config.token:
            self.show_client_config_dialog()

    def init_ui(self):
        """初始化UI"""
        # 中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # 主布局
        main_layout = QVBoxLayout()

        # 分割器
        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧项目面板
        self.project_ui = ProjectUI(self.client_config)
        self.project_ui.project_selected.connect(self.on_project_selected)
        self.splitter.addWidget(self.project_ui)

        # 右侧标签页
        self.tab_widget = QTabWidget()

        # 文件管理
        self.files_ui = FilesUI(self.client_config)
        self.tab_widget.addTab(self.files_ui, "文件管理")

        # 词条管理
        self.strings_ui = StringsUI(self.client_config)
        self.tab_widget.addTab(self.strings_ui, "词条管理")

        # 术语管理
        self.terms_ui = TermsUI(self.client_config)
        self.tab_widget.addTab(self.terms_ui, "术语管理")

        # 导出管理
        self.export_ui = ExportUI(self.client_config)
        self.tab_widget.addTab(self.export_ui, "导出管理")

        # 成员管理
        self.members_ui = MembersUI(self.client_config)
        self.tab_widget.addTab(self.members_ui, "成员管理")

        # 历史记录
        self.history_ui = HistoryUI(self.client_config)
        self.tab_widget.addTab(self.history_ui, "历史记录")

        # 贡献统计
        self.contribution_ui = ContributionUI(self.client_config)
        self.tab_widget.addTab(self.contribution_ui, "贡献统计")

        # 用户管理
        self.user_ui = UserUI(self.client_config)
        self.tab_widget.addTab(self.user_ui, "用户管理")

        self.splitter.addWidget(self.tab_widget)
        self.splitter.setSizes([400, 800])

        main_layout.addWidget(self.splitter)
        central_widget.setLayout(main_layout)

    def init_menu(self):
        """初始化菜单栏"""
        menubar = self.menuBar()

        # 文件菜单
        file_menu = menubar.addMenu("文件")

        config_action = file_menu.addAction("配置")
        config_action.triggered.connect(self.show_client_config_dialog)

        file_menu.addSeparator()

        exit_action = file_menu.addAction("退出")
        exit_action.triggered.connect(self.close)

        # 帮助菜单
        help_menu = menubar.addMenu("帮助")

        about_action = help_menu.addAction("关于")
        about_action.triggered.connect(self.show_about_dialog)

    def init_status_bar(self):
        """初始化状态栏"""
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("就绪")

    def show_client_config_dialog(self):
        """显示客户端配置对话框"""
        dialog = ParatranzClientUI(self, self.client_config)
        dialog.config_updated.connect(self.update_client_config)
        dialog.exec()

    def update_client_config(self, config):
        """更新客户端配置"""
        self.client_config = config

        # 更新所有UI组件的配置
        self.project_ui.client_config = config
        self.project_ui.api.config = config

        self.files_ui.client_config = config
        self.files_ui.api.config = config

        self.strings_ui.client_config = config
        self.strings_ui.api.config = config

        self.terms_ui.client_config = config
        self.terms_ui.api.config = config

        self.export_ui.client_config = config
        self.export_ui.api.config = config

        self.members_ui.client_config = config
        self.members_ui.api.config = config

        self.history_ui.client_config = config
        self.history_ui.api.config = config

        self.contribution_ui.client_config = config
        self.contribution_ui.api.config = config

        self.user_ui.client_config = config
        self.user_ui.api.config = config

        self.status_bar.showMessage("配置已更新")

    def on_project_selected(self, project):
        """项目选择变化处理"""
        project_id = project.get("id")

        # 更新所有UI组件的项目ID
        self.files_ui.set_project_id(project_id)
        self.strings_ui.set_project_id(project_id)
        self.terms_ui.set_project_id(project_id)
        self.export_ui.set_project_id(project_id)
        self.members_ui.set_project_id(project_id)
        self.history_ui.set_project_id(project_id)
        self.contribution_ui.set_project_id(project_id)

        self.status_bar.showMessage(f"已选择项目: {project.get('name', '')}")

    def show_about_dialog(self):
        """显示关于对话框"""
        QMessageBox.about(
            self, 
            "关于 TransBridge",
            "TransBridge - Paratranz 翻译管理工具\n\n"
            "版本: 1.0.0\n\n"
            "作者: TransBridge Team\n\n"
            "本工具用于管理和操作 Paratranz 翻译平台的项目、文件、词条等数据。"
        )
