from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QDialog, QFormLayout, QMessageBox, QComboBox, QGroupBox,
    QTabWidget, QTextEdit, QSplitter, QFrame, QSpinBox,
    QCheckBox
)
from PyQt6.QtCore import pyqtSignal, Qt
from src.transbridge.paratranz.api.paratranz_members_api import ParatranzMembersAPI


class MemberCreateDialog(QDialog):
    """
    创建成员对话框
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加成员")
        self.resize(400, 300)

        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # 表单布局
        form_layout = QFormLayout()

        # 用户ID
        self.user_id_input = QSpinBox()
        self.user_id_input.setMinimum(1)
        self.user_id_input.setMaximum(999999)
        form_layout.addRow("用户ID:", self.user_id_input)

        # 角色
        self.role_input = QComboBox()
        self.role_input.addItems([
            "translator", "reviewer", "manager", "admin"
        ])
        form_layout.addRow("角色:", self.role_input)

        # 语言
        self.languages_group = QGroupBox("可翻译语言")
        languages_layout = QVBoxLayout()

        self.language_checkboxes = []
        languages = ["zh-CN", "zh-TW", "ja", "ko", "fr", "de", "es", "ru", "pt"]

        for lang in languages:
            checkbox = QCheckBox(lang)
            self.language_checkboxes.append(checkbox)
            languages_layout.addWidget(checkbox)

        self.languages_group.setLayout(languages_layout)
        form_layout.addRow(self.languages_group)

        layout.addLayout(form_layout)

        # 按钮区域
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.create_button = QPushButton("添加")
        self.create_button.clicked.connect(self.accept)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.reject)

        button_layout.addWidget(self.create_button)
        button_layout.addWidget(self.cancel_button)

        layout.addLayout(button_layout)

        self.setLayout(layout)

    def get_member_data(self):
        """获取成员数据"""
        languages = [cb.text() for cb in self.language_checkboxes if cb.isChecked()]

        return {
            "userId": self.user_id_input.value(),
            "role": self.role_input.currentText(),
            "languages": languages
        }


class MemberEditDialog(MemberCreateDialog):
    """
    编辑成员对话框
    """

    def __init__(self, member_data, parent=None):
        self.member_data = member_data
        super().__init__(parent)
        self.setWindowTitle("编辑成员")
        self.load_member_data()

    def load_member_data(self):
        """加载成员数据到表单"""
        self.user_id_input.setValue(self.member_data.get("userId", 1))
        self.user_id_input.setEnabled(False)  # 用户ID不可编辑

        role = self.member_data.get("role", "")
        index = self.role_input.findText(role)
        if index >= 0:
            self.role_input.setCurrentIndex(index)

        # 设置语言选择
        languages = self.member_data.get("languages", [])
        for checkbox in self.language_checkboxes:
            if checkbox.text() in languages:
                checkbox.setChecked(True)


class MembersUI(QWidget):
    """
    Paratranz 成员管理界面
    """

    # 信号：成员被选中
    member_selected = pyqtSignal(dict)

    def __init__(self, client_config, parent=None):
        super().__init__(parent)
        self.client_config = client_config
        self.api = ParatranzMembersAPI(
            token=client_config.token,
            timeout=client_config.timeout,
            config=client_config
        )

        self.members = []
        self.current_member = None
        self.project_id = None

        self.init_ui()

    def init_ui(self):
        # 主布局
        main_layout = QVBoxLayout()

        # 顶部工具栏
        toolbar_layout = QHBoxLayout()

        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.load_members)

        self.add_button = QPushButton("添加成员")
        self.add_button.clicked.connect(self.add_member)

        self.edit_button = QPushButton("编辑成员")
        self.edit_button.clicked.connect(self.edit_member)
        self.edit_button.setEnabled(False)

        self.delete_button = QPushButton("删除成员")
        self.delete_button.clicked.connect(self.delete_member)
        self.delete_button.setEnabled(False)

        toolbar_layout.addWidget(self.refresh_button)
        toolbar_layout.addWidget(self.add_button)
        toolbar_layout.addWidget(self.edit_button)
        toolbar_layout.addWidget(self.delete_button)
        toolbar_layout.addStretch()

        main_layout.addLayout(toolbar_layout)

        # 分割器
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧成员列表
        left_frame = QFrame()
        left_layout = QVBoxLayout()

        # 成员表格
        self.member_table = QTableWidget()
        self.member_table.setColumnCount(5)
        self.member_table.setHorizontalHeaderLabels(["ID", "用户名", "角色", "语言", "加入时间"])
        self.member_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.member_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.member_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.member_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.member_table.itemSelectionChanged.connect(self.on_member_selection_changed)
        self.member_table.itemDoubleClicked.connect(self.on_member_double_clicked)

        left_layout.addWidget(self.member_table)
        left_frame.setLayout(left_layout)

        # 右侧成员详情
        right_frame = QFrame()
        right_layout = QVBoxLayout()

        self.member_details = QTabWidget()

        # 基本信息标签页
        self.info_tab = QWidget()
        info_layout = QVBoxLayout()

        self.member_info = QTextEdit()
        self.member_info.setReadOnly(True)
        self.member_info.setPlainText("请选择一个成员查看详情")

        info_layout.addWidget(self.member_info)
        self.info_tab.setLayout(info_layout)

        self.member_details.addTab(self.info_tab, "基本信息")

        right_layout.addWidget(self.member_details)
        right_frame.setLayout(right_layout)

        splitter.addWidget(left_frame)
        splitter.addWidget(right_frame)
        splitter.setSizes([400, 400])

        main_layout.addWidget(splitter)

        self.setLayout(main_layout)

    def set_project_id(self, project_id):
        """设置项目ID"""
        self.project_id = project_id
        if project_id:
            self.load_members()

    def load_members(self):
        """加载成员列表"""
        if not self.project_id:
            return

        try:
            self.members = self.api.list_members(self.project_id)
            self.update_member_table()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载成员列表失败: {str(e)}")

    def update_member_table(self):
        """更新成员表格"""
        self.member_table.setRowCount(len(self.members))

        for row, member in enumerate(self.members):
            # 成员ID
            id_item = QTableWidgetItem(str(member.get("id", "")))
            self.member_table.setItem(row, 0, id_item)

            # 用户名
            username = member.get("user", {}).get("username", "")
            username_item = QTableWidgetItem(username)
            self.member_table.setItem(row, 1, username_item)

            # 角色
            role_item = QTableWidgetItem(member.get("role", ""))
            self.member_table.setItem(row, 2, role_item)

            # 语言
            languages = member.get("languages", [])
            languages_text = ", ".join(languages) if languages else ""
            languages_item = QTableWidgetItem(languages_text)
            self.member_table.setItem(row, 3, languages_item)

            # 加入时间
            created_at = member.get("createdAt", "")
            created_at_item = QTableWidgetItem(created_at)
            self.member_table.setItem(row, 4, created_at_item)

            # 存储完整成员数据
            self.member_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, member)

    def on_member_selection_changed(self):
        """成员选择变化处理"""
        selected_items = self.member_table.selectedItems()
        has_selection = len(selected_items) > 0

        self.edit_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)

        if has_selection:
            # 获取成员数据
            member = selected_items[0].data(Qt.ItemDataRole.UserRole)
            self.current_member = member
            self.show_member_details(member)
            self.member_selected.emit(member)
        else:
            self.current_member = None
            self.member_info.setPlainText("请选择一个成员查看详情")

    def on_member_double_clicked(self, item):
        """成员双击处理"""
        member = item.data(Qt.ItemDataRole.UserRole)
        self.member_selected.emit(member)

    def show_member_details(self, member):
        """显示成员详情"""
        user = member.get("user", {})

        details = f"成员ID: {member.get('id', '')}"
        details += f"用户ID: {member.get('userId', '')}"
        details += f"用户名: {user.get('username', '')}"
        details += f"昵称: {user.get('nickname', '')}"
        details += f"邮箱: {user.get('email', '')}"
        details += f"角色: {member.get('role', '')}"

        languages = member.get("languages", [])
        details += f"可翻译语言: {', '.join(languages) if languages else '无'}"

        details += f"加入时间: {member.get('createdAt', '')}"
        details += f"更新时间: {member.get('updatedAt', '')}"

        self.member_info.setPlainText(details)

    def add_member(self):
        """添加成员"""
        if not self.project_id:
            QMessageBox.warning(self, "警告", "请先选择一个项目")
            return

        dialog = MemberCreateDialog(self)
        if dialog.exec():
            member_data = dialog.get_member_data()
            try:
                new_member = self.api.create_member(self.project_id, member_data)
                QMessageBox.information(self, "成功", "成员添加成功!")
                self.load_members()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"添加成员失败: {str(e)}")

    def edit_member(self):
        """编辑成员"""
        if not self.current_member:
            return

        dialog = MemberEditDialog(self.current_member, self)
        if dialog.exec():
            member_data = dialog.get_member_data()
            try:
                self.api.update_member(
                    self.project_id,
                    self.current_member["id"],
                    member_data
                )
                QMessageBox.information(self, "成功", "成员信息更新成功!")
                self.load_members()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"更新成员信息失败: {str(e)}")

    def delete_member(self):
        """删除成员"""
        if not self.current_member:
            return

        username = self.current_member.get("user", {}).get("username", "")
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除成员 '{username}' 吗?\n此操作不可撤销!",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.api.delete_member(self.project_id, self.current_member["id"])
                QMessageBox.information(self, "成功", "成员删除成功!")
                self.load_members()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"删除成员失败: {str(e)}")
