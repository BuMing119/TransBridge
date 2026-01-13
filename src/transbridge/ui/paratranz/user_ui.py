from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QDialog, QFormLayout, QMessageBox, QGroupBox,
    QSplitter, QFrame, QSpinBox,
    QCheckBox
)
from PyQt6.QtCore import pyqtSignal, Qt
from src.transbridge.paratranz.api.paratranz_user_api import ParatranzUserAPI


class UserEditDialog(QDialog):
    """
    编辑用户对话框
    """

    def __init__(self, user_data, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑用户信息")
        self.resize(400, 300)

        self.user_data = user_data
        self.init_ui()
        self.load_user_data()

    def init_ui(self):
        layout = QVBoxLayout()

        # 表单布局
        form_layout = QFormLayout()

        # 用户名
        self.username_input = QLineEdit()
        form_layout.addRow("用户名:", self.username_input)

        # 邮箱
        self.email_input = QLineEdit()
        form_layout.addRow("邮箱:", self.email_input)

        # 语言
        self.languages_group = QGroupBox("语言")
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

        self.save_button = QPushButton("保存")
        self.save_button.clicked.connect(self.accept)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.reject)

        button_layout.addWidget(self.save_button)
        button_layout.addWidget(self.cancel_button)

        layout.addLayout(button_layout)

        self.setLayout(layout)

    def load_user_data(self):
        """加载用户数据到表单"""
        self.username_input.setText(self.user_data.get("username", ""))
        self.email_input.setText(self.user_data.get("email", ""))

        # 设置语言选择
        languages = self.user_data.get("languages", [])
        for checkbox in self.language_checkboxes:
            if checkbox.text() in languages:
                checkbox.setChecked(True)

    def get_user_data(self):
        """获取用户数据"""
        languages = [cb.text() for cb in self.language_checkboxes if cb.isChecked()]

        return {
            "name": self.username_input.text().strip(),
            "email": self.email_input.text().strip(),
            "languages": languages
        }


class UserUI(QWidget):
    """
    Paratranz 用户管理界面
    """

    # 信号：用户被选中
    user_selected = pyqtSignal(dict)

    def __init__(self, client_config, parent=None):
        super().__init__(parent)
        self.client_config = client_config
        self.api = ParatranzUserAPI(
            token=client_config.token,
            timeout=client_config.timeout,
            config=client_config
        )

        self.current_user = None
        self.current_page = 1
        self.total_pages = 1

        self.init_ui()

    def init_ui(self):
        # 主布局
        main_layout = QVBoxLayout()

        # 顶部工具栏
        toolbar_layout = QHBoxLayout()

        self.search_button = QPushButton("搜索")
        self.search_button.clicked.connect(self.search_user)

        self.edit_button = QPushButton("编辑用户")
        self.edit_button.clicked.connect(self.edit_user)
        self.edit_button.setEnabled(False)

        self.view_history_button = QPushButton("查看历史")
        self.view_history_button.clicked.connect(self.view_user_history)
        self.view_history_button.setEnabled(False)

        toolbar_layout.addWidget(QLabel("用户ID:"))
        self.user_id_input = QSpinBox()
        self.user_id_input.setMinimum(1)
        self.user_id_input.setMaximum(999999)
        toolbar_layout.addWidget(self.user_id_input)

        toolbar_layout.addWidget(self.search_button)
        toolbar_layout.addWidget(self.edit_button)
        toolbar_layout.addWidget(self.view_history_button)
        toolbar_layout.addStretch()

        main_layout.addLayout(toolbar_layout)

        # 分割器
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧用户信息
        left_frame = QFrame()
        left_layout = QVBoxLayout()

        # 用户信息表单
        self.user_info_group = QGroupBox("用户信息")
        user_info_layout = QFormLayout()

        self.id_label = QLabel("-")
        self.username_label = QLabel("-")
        self.email_label = QLabel("-")
        self.nickname_label = QLabel("-")
        self.languages_label = QLabel("-")
        self.created_at_label = QLabel("-")

        user_info_layout.addRow("ID:", self.id_label)
        user_info_layout.addRow("用户名:", self.username_label)
        user_info_layout.addRow("邮箱:", self.email_label)
        user_info_layout.addRow("昵称:", self.nickname_label)
        user_info_layout.addRow("语言:", self.languages_label)
        user_info_layout.addRow("注册时间:", self.created_at_label)

        self.user_info_group.setLayout(user_info_layout)
        left_layout.addWidget(self.user_info_group)

        left_frame.setLayout(left_layout)

        # 右侧用户历史记录
        right_frame = QFrame()
        right_layout = QVBoxLayout()

        # 历史记录表格
        self.history_table = QTableWidget()
        self.history_table.setColumnCount(4)
        self.history_table.setHorizontalHeaderLabels(["时间", "项目", "操作", "详情"])
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.history_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        right_layout.addWidget(self.history_table)

        # 分页区域
        page_layout = QHBoxLayout()

        page_layout.addWidget(QLabel("页码:"))
        self.page_input = QSpinBox()
        self.page_input.setMinimum(1)
        self.page_input.valueChanged.connect(self.on_page_changed)
        page_layout.addWidget(self.page_input)

        self.page_label = QLabel("/ 1")
        page_layout.addWidget(self.page_label)

        page_layout.addStretch()

        right_layout.addLayout(page_layout)

        right_frame.setLayout(right_layout)

        splitter.addWidget(left_frame)
        splitter.addWidget(right_frame)
        splitter.setSizes([300, 500])

        main_layout.addWidget(splitter)

        self.setLayout(main_layout)

    def search_user(self):
        """搜索用户"""
        user_id = self.user_id_input.value()

        try:
            self.current_user = self.api.get_user(user_id)
            self.show_user_info(self.current_user)
            self.load_user_history()

            self.edit_button.setEnabled(True)
            self.view_history_button.setEnabled(True)

            self.user_selected.emit(self.current_user)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"获取用户信息失败: {str(e)}")
            self.clear_user_info()
            self.edit_button.setEnabled(False)
            self.view_history_button.setEnabled(False)

    def show_user_info(self, user):
        """显示用户信息"""
        self.id_label.setText(str(user.get("id", "")))
        self.username_label.setText(user.get("username", ""))
        self.email_label.setText(user.get("email", ""))
        self.nickname_label.setText(user.get("nickname", ""))

        languages = user.get("languages", [])
        self.languages_label.setText(", ".join(languages) if languages else "-")

        self.created_at_label.setText(user.get("createdAt", ""))

    def clear_user_info(self):
        """清空用户信息"""
        self.id_label.setText("-")
        self.username_label.setText("-")
        self.email_label.setText("-")
        self.nickname_label.setText("-")
        self.languages_label.setText("-")
        self.created_at_label.setText("-")

        self.history_table.setRowCount(0)
        self.current_user = None

    def load_user_history(self, page=1):
        """加载用户历史记录"""
        if not self.current_user:
            return

        try:
            result = self.api.get_user_history(self.current_user["id"], page)
            histories = result.get("docs", [])
            self.total_pages = result.get("pages", 1)
            self.current_page = result.get("page", 1)

            self.update_history_table(histories)
            self.update_page_controls()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载用户历史记录失败: {str(e)}")

    def update_history_table(self, histories):
        """更新历史记录表格"""
        self.history_table.setRowCount(len(histories))

        for row, history in enumerate(histories):
            # 时间
            time_item = QTableWidgetItem(history.get("createdAt", ""))
            self.history_table.setItem(row, 0, time_item)

            # 项目
            project = history.get("project", {})
            project_name = project.get("name", "")
            project_item = QTableWidgetItem(project_name)
            self.history_table.setItem(row, 1, project_item)

            # 操作
            action = history.get("action", "")
            action_item = QTableWidgetItem(action)
            self.history_table.setItem(row, 2, action_item)

            # 详情
            details = ""
            if "string" in history:
                details = f"词条: {history['string'].get('key', '')}"
            elif "term" in history:
                details = f"术语: {history['term'].get('key', '')}"

            details_item = QTableWidgetItem(details)
            self.history_table.setItem(row, 3, details_item)

    def update_page_controls(self):
        """更新分页控件"""
        self.page_input.setValue(self.current_page)
        self.page_input.setMaximum(self.total_pages)
        self.page_label.setText(f"/ {self.total_pages}")

    def on_page_changed(self, page):
        """页码变化处理"""
        if page != self.current_page:
            self.load_user_history(page)

    def edit_user(self):
        """编辑用户"""
        if not self.current_user:
            return

        dialog = UserEditDialog(self.current_user, self)
        if dialog.exec():
            user_data = dialog.get_user_data()
            try:
                self.api.update_user(self.current_user["id"], user_data)
                QMessageBox.information(self, "成功", "用户信息更新成功!")
                self.search_user()  # 重新加载用户信息
            except Exception as e:
                QMessageBox.critical(self, "错误", f"更新用户信息失败: {str(e)}")

    def view_user_history(self):
        """查看用户历史记录"""
        if not self.current_user:
            return

        # 已经在右侧显示历史记录
        pass
