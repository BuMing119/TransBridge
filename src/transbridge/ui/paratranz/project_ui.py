
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QDialog, QFormLayout, QMessageBox, QComboBox, QTabWidget, QTextEdit, QSplitter, QFrame
)
from PyQt6.QtCore import pyqtSignal, Qt
from src.transbridge.paratranz.api.paratranz_project_api import ParatranzProjectAPI


class ProjectCreateDialog(QDialog):
    """
    创建项目对话框
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("创建新项目")
        self.resize(400, 300)

        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # 表单布局
        form_layout = QFormLayout()

        # 项目名称
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("输入项目名称")
        form_layout.addRow("项目名称:", self.name_input)

        # 源语言
        self.source_lang_input = QComboBox()
        self.source_lang_input.setEditable(True)
        self.source_lang_input.addItems([
            "en", "zh-CN", "zh-TW", "ja", "ko", "fr", "de", "es", "ru", "pt"
        ])
        form_layout.addRow("源语言:", self.source_lang_input)

        # 项目描述
        self.description_input = QTextEdit()
        self.description_input.setPlaceholderText("输入项目描述")
        self.description_input.setMaximumHeight(100)
        form_layout.addRow("项目描述:", self.description_input)

        layout.addLayout(form_layout)

        # 按钮区域
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.create_button = QPushButton("创建")
        self.create_button.clicked.connect(self.accept)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.reject)

        button_layout.addWidget(self.create_button)
        button_layout.addWidget(self.cancel_button)

        layout.addLayout(button_layout)

        self.setLayout(layout)

    def get_project_data(self):
        """获取项目数据"""
        return {
            "name": self.name_input.text().strip(),
            "sourceLanguage": self.source_lang_input.currentText(),
            "description": self.description_input.toPlainText().strip()
        }


class ProjectEditDialog(ProjectCreateDialog):
    """
    编辑项目对话框
    """

    def __init__(self, project_data, parent=None):
        self.project_data = project_data
        super().__init__(parent)
        self.setWindowTitle("编辑项目")
        self.load_project_data()

    def load_project_data(self):
        """加载项目数据到表单"""
        self.name_input.setText(self.project_data.get("name", ""))
        self.source_lang_input.setCurrentText(self.project_data.get("sourceLanguage", ""))
        self.description_input.setPlainText(self.project_data.get("description", ""))


class ProjectUI(QWidget):
    """
    Paratranz 项目管理界面
    """

    # 信号：项目被选中
    project_selected = pyqtSignal(dict)

    def __init__(self, client_config, parent=None):
        super().__init__(parent)
        self.client_config = client_config
        self.api = ParatranzProjectAPI(
            token=client_config.token,
            timeout=client_config.timeout,
            config=client_config
        )

        self.projects = []
        self.current_project = None

        self.init_ui()
        self.load_projects()

    def init_ui(self):
        # 主布局
        main_layout = QVBoxLayout()

        # 顶部工具栏
        toolbar_layout = QHBoxLayout()

        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.load_projects)

        self.create_button = QPushButton("新建项目")
        self.create_button.clicked.connect(self.create_project)

        self.edit_button = QPushButton("编辑项目")
        self.edit_button.clicked.connect(self.edit_project)
        self.edit_button.setEnabled(False)

        self.delete_button = QPushButton("删除项目")
        self.delete_button.clicked.connect(self.delete_project)
        self.delete_button.setEnabled(False)

        toolbar_layout.addWidget(self.refresh_button)
        toolbar_layout.addWidget(self.create_button)
        toolbar_layout.addWidget(self.edit_button)
        toolbar_layout.addWidget(self.delete_button)
        toolbar_layout.addStretch()

        main_layout.addLayout(toolbar_layout)

        # 分割器
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧项目列表
        left_frame = QFrame()
        left_layout = QVBoxLayout()

        # 项目表格
        self.project_table = QTableWidget()
        self.project_table.setColumnCount(3)
        self.project_table.setHorizontalHeaderLabels(["ID", "名称", "源语言"])
        self.project_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.project_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.project_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.project_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.project_table.itemSelectionChanged.connect(self.on_project_selection_changed)
        self.project_table.itemDoubleClicked.connect(self.on_project_double_clicked)

        left_layout.addWidget(self.project_table)
        left_frame.setLayout(left_layout)

        # 右侧项目详情
        right_frame = QFrame()
        right_layout = QVBoxLayout()

        self.project_details = QTabWidget()

        # 基本信息标签页
        self.info_tab = QWidget()
        info_layout = QVBoxLayout()

        self.project_info = QTextEdit()
        self.project_info.setReadOnly(True)
        self.project_info.setPlainText("请选择一个项目查看详情")

        info_layout.addWidget(self.project_info)
        self.info_tab.setLayout(info_layout)

        self.project_details.addTab(self.info_tab, "基本信息")

        right_layout.addWidget(self.project_details)
        right_frame.setLayout(right_layout)

        splitter.addWidget(left_frame)
        splitter.addWidget(right_frame)
        splitter.setSizes([300, 500])

        main_layout.addWidget(splitter)

        self.setLayout(main_layout)

    def load_projects(self):
        """加载项目列表"""
        try:
            response = self.api.list_projects()

            # 检查返回的数据格式
            if isinstance(response, dict):
                # 尝试从字典中提取项目列表，常见的关键字有data, results, items等
                if 'data' in response and isinstance(response['data'], list):
                    self.projects = response['data']
                elif 'results' in response and isinstance(response['results'], list):
                    self.projects = response['results']
                elif 'items' in response and isinstance(response['items'], list):
                    self.projects = response['items']
                else:
                    # 如果字典中包含多个列表，尝试找到包含项目数据的列表
                    project_list = None
                    for key, value in response.items():
                        if isinstance(value, list) and len(value) > 0 and isinstance(value[0], dict):
                            # 检查列表中的字典是否包含项目特征字段
                            if 'id' in value[0] or 'name' in value[0]:
                                project_list = value
                                break

                    if project_list is not None:
                        self.projects = project_list
                    else:
                        raise TypeError(f"无法从API返回的字典中找到项目列表: {list(response.keys())}")
            elif isinstance(response, list):
                self.projects = response
            else:
                raise TypeError(f"API返回的数据格式错误，期望列表或字典，实际得到: {type(response)}")

            # 验证列表中的每个元素是否为字典
            for project in self.projects:
                if not isinstance(project, dict):
                    raise TypeError(f"项目数据格式错误，期望字典，实际得到: {type(project)}")

            self.update_project_table()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载项目列表失败: {str(e)}")

    def update_project_table(self):
        """更新项目表格"""
        self.project_table.setRowCount(len(self.projects))

        for row, project in enumerate(self.projects):
            # 项目ID
            id_item = QTableWidgetItem(str(project.get("id", "")))
            self.project_table.setItem(row, 0, id_item)

            # 项目名称
            name_item = QTableWidgetItem(project.get("name", ""))
            self.project_table.setItem(row, 1, name_item)

            # 源语言
            source_lang_item = QTableWidgetItem(project.get("sourceLanguage", ""))
            self.project_table.setItem(row, 2, source_lang_item)

            # 存储完整项目数据
            self.project_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, project)

    def on_project_selection_changed(self):
        """项目选择变化处理"""
        selected_items = self.project_table.selectedItems()
        has_selection = len(selected_items) > 0

        self.edit_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)

        if has_selection:
            # 获取项目数据
            project = selected_items[0].data(Qt.ItemDataRole.UserRole)
            self.current_project = project
            self.show_project_details(project)
            self.project_selected.emit(project)
        else:
            self.current_project = None
            self.project_info.setPlainText("请选择一个项目查看详情")

    def on_project_double_clicked(self, item):
        """项目双击处理"""
        project = item.data(Qt.ItemDataRole.UserRole)
        self.project_selected.emit(project)

    def show_project_details(self, project):
        """显示项目详情"""
        # 检查project是否为字典类型
        if not isinstance(project, dict):
            self.project_info.setPlainText("项目数据格式错误")
            return

        details = f"项目ID: {project.get('id', '')}"
        details += f"\n项目名称: {project.get('name', '')}"
        details += f"\n源语言: {project.get('sourceLanguage', '')}"
        details += f"\n描述: {project.get('description', '')}"
        details += f"\n创建时间: {project.get('createdAt', '')}"
        details += f"\n更新时间: {project.get('updatedAt', '')}"

        self.project_info.setPlainText(details)

    def create_project(self):
        """创建新项目"""
        dialog = ProjectCreateDialog(self)
        if dialog.exec():
            project_data = dialog.get_project_data()
            try:
                new_project = self.api.create_project(project_data)
                QMessageBox.information(self, "成功", "项目创建成功!")
                self.load_projects()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"创建项目失败: {str(e)}")

    def edit_project(self):
        """编辑项目"""
        if not self.current_project:
            return

        dialog = ProjectEditDialog(self.current_project, self)
        if dialog.exec():
            project_data = dialog.get_project_data()
            try:
                self.api.update_project(self.current_project["id"], project_data)
                QMessageBox.information(self, "成功", "项目更新成功!")
                self.load_projects()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"更新项目失败: {str(e)}")

    def delete_project(self):
        """删除项目"""
        if not self.current_project:
            return

        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除项目 '{self.current_project.get('name', '')}' 吗?\n此操作不可撤销!",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.api.delete_project(self.current_project["id"])
                QMessageBox.information(self, "成功", "项目删除成功!")
                self.load_projects()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"删除项目失败: {str(e)}")
