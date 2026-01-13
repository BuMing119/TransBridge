from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QDialog, QFormLayout, QMessageBox, QComboBox, QTabWidget, QTextEdit, QSplitter, QFrame, QSpinBox,
    QDateEdit
)
from PyQt6.QtCore import pyqtSignal, Qt, QDate
from src.transbridge.paratranz.api.paratranz_history_api import ParatranzHistoryAPI


class HistoryFilterDialog(QDialog):
    """
    历史记录筛选对话框
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("筛选历史记录")
        self.resize(400, 300)

        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # 表单布局
        form_layout = QFormLayout()

        # 起始日期
        self.start_date_input = QDateEdit(calendarPopup=True)
        self.start_date_input.setDate(QDate.currentDate().addMonths(-1))
        form_layout.addRow("起始日期:", self.start_date_input)

        # 结束日期
        self.end_date_input = QDateEdit(calendarPopup=True)
        self.end_date_input.setDate(QDate.currentDate())
        form_layout.addRow("结束日期:", self.end_date_input)

        # 操作类型
        self.action_type_input = QComboBox()
        self.action_type_input.addItems([
            "", "create", "update", "delete", "approve", "reject"
        ])
        form_layout.addRow("操作类型:", self.action_type_input)

        layout.addLayout(form_layout)

        # 按钮区域
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.apply_button = QPushButton("应用")
        self.apply_button.clicked.connect(self.accept)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.reject)

        button_layout.addWidget(self.apply_button)
        button_layout.addWidget(self.cancel_button)

        layout.addLayout(button_layout)

        self.setLayout(layout)

    def get_filter_options(self):
        """获取筛选选项"""
        return {
            "start_date": self.start_date_input.date().toString(Qt.DateFormat.ISODate),
            "end_date": self.end_date_input.date().toString(Qt.DateFormat.ISODate),
            "action_type": self.action_type_input.currentText().strip()
        }


class HistoryUI(QWidget):
    """
    Paratranz 历史记录管理界面
    """

    # 信号：历史记录被选中
    history_selected = pyqtSignal(dict)

    def __init__(self, client_config, parent=None):
        super().__init__(parent)
        self.client_config = client_config
        self.api = ParatranzHistoryAPI(
            token=client_config.token,
            timeout=client_config.timeout,
            config=client_config
        )

        self.histories = []
        self.current_history = None
        self.project_id = None
        self.file_id = None
        self.term_id = None
        self.current_page = 1
        self.total_pages = 1
        self.history_type = "project"  # project, file, term

        self.filter_options = {}

        self.init_ui()

    def init_ui(self):
        # 主布局
        main_layout = QVBoxLayout()

        # 顶部工具栏
        toolbar_layout = QHBoxLayout()

        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.load_histories)

        self.filter_button = QPushButton("筛选")
        self.filter_button.clicked.connect(self.show_filter_dialog)

        toolbar_layout.addWidget(self.refresh_button)
        toolbar_layout.addWidget(self.filter_button)
        toolbar_layout.addStretch()

        # 历史类型选择
        self.history_type_input = QComboBox()
        self.history_type_input.addItems(["项目历史", "文件历史", "术语历史"])
        self.history_type_input.currentTextChanged.connect(self.on_history_type_changed)
        toolbar_layout.addWidget(QLabel("历史类型:"))
        toolbar_layout.addWidget(self.history_type_input)

        main_layout.addLayout(toolbar_layout)

        # 筛选信息
        self.filter_info = QLabel("未应用筛选")
        main_layout.addWidget(self.filter_info)

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

        main_layout.addLayout(page_layout)

        # 分割器
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧历史记录列表
        left_frame = QFrame()
        left_layout = QVBoxLayout()

        # 历史记录表格
        self.history_table = QTableWidget()
        self.history_table.setColumnCount(5)
        self.history_table.setHorizontalHeaderLabels(["时间", "用户", "操作", "对象", "详情"])
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.history_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_table.itemSelectionChanged.connect(self.on_history_selection_changed)
        self.history_table.itemDoubleClicked.connect(self.on_history_double_clicked)

        left_layout.addWidget(self.history_table)
        left_frame.setLayout(left_layout)

        # 右侧历史记录详情
        right_frame = QFrame()
        right_layout = QVBoxLayout()

        self.history_details = QTabWidget()

        # 基本信息标签页
        self.info_tab = QWidget()
        info_layout = QVBoxLayout()

        self.history_info = QTextEdit()
        self.history_info.setReadOnly(True)
        self.history_info.setPlainText("请选择一条历史记录查看详情")

        info_layout.addWidget(self.history_info)
        self.info_tab.setLayout(info_layout)

        self.history_details.addTab(self.info_tab, "基本信息")

        # 变更对比标签页
        self.diff_tab = QWidget()
        diff_layout = QVBoxLayout()

        self.diff_info = QTextEdit()
        self.diff_info.setReadOnly(True)
        self.diff_info.setPlaceholderText("变更对比信息将在此显示...")

        diff_layout.addWidget(self.diff_info)
        self.diff_tab.setLayout(diff_layout)

        self.history_details.addTab(self.diff_tab, "变更对比")

        right_layout.addWidget(self.history_details)
        right_frame.setLayout(right_layout)

        splitter.addWidget(left_frame)
        splitter.addWidget(right_frame)
        splitter.setSizes([500, 500])

        main_layout.addWidget(splitter)

        self.setLayout(main_layout)

    def set_project_id(self, project_id):
        """设置项目ID"""
        self.project_id = project_id
        self.load_histories()

    def set_file_id(self, file_id):
        """设置文件ID"""
        self.file_id = file_id
        self.history_type = "file"
        self.history_type_input.setCurrentIndex(1)
        self.load_histories()

    def set_term_id(self, term_id):
        """设置术语ID"""
        self.term_id = term_id
        self.history_type = "term"
        self.history_type_input.setCurrentIndex(2)
        self.load_histories()

    def on_history_type_changed(self, text):
        """历史类型变化处理"""
        if text == "项目历史":
            self.history_type = "project"
        elif text == "文件历史":
            self.history_type = "file"
        elif text == "术语历史":
            self.history_type = "term"

        self.load_histories()

    def show_filter_dialog(self):
        """显示筛选对话框"""
        dialog = HistoryFilterDialog(self)
        if dialog.exec():
            self.filter_options = dialog.get_filter_options()
            self.update_filter_info()
            self.load_histories()

    def update_filter_info(self):
        """更新筛选信息显示"""
        info_parts = []

        if self.filter_options.get("start_date"):
            info_parts.append(f"起始日期: {self.filter_options['start_date']}")

        if self.filter_options.get("end_date"):
            info_parts.append(f"结束日期: {self.filter_options['end_date']}")

        if self.filter_options.get("action_type"):
            info_parts.append(f"操作类型: {self.filter_options['action_type']}")

        if info_parts:
            self.filter_info.setText(" | ".join(info_parts))
        else:
            self.filter_info.setText("未应用筛选")

    def load_histories(self, page=1):
        """加载历史记录"""
        try:
            if self.history_type == "project" and self.project_id:
                result = self.api.get_project_history(self.project_id, page)
            elif self.history_type == "file" and self.project_id and self.file_id:
                result = self.api.get_file_history(self.project_id, self.file_id, page)
            elif self.history_type == "term" and self.project_id and self.term_id:
                result = self.api.get_term_history(self.project_id, self.term_id, page)
            else:
                QMessageBox.warning(self, "警告", "请选择相应的项目、文件或术语")
                return

            self.histories = result.get("docs", [])
            self.total_pages = result.get("pages", 1)
            self.current_page = result.get("page", 1)

            self.update_history_table()
            self.update_page_controls()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载历史记录失败: {str(e)}")

    def update_history_table(self):
        """更新历史记录表格"""
        self.history_table.setRowCount(len(self.histories))

        for row, history in enumerate(self.histories):
            # 时间
            time_item = QTableWidgetItem(history.get("createdAt", ""))
            self.history_table.setItem(row, 0, time_item)

            # 用户
            user = history.get("user", {})
            username = user.get("username", "")
            user_item = QTableWidgetItem(username)
            self.history_table.setItem(row, 1, user_item)

            # 操作
            action = history.get("action", "")
            action_item = QTableWidgetItem(action)
            self.history_table.setItem(row, 2, action_item)

            # 对象
            target = ""
            if "string" in history:
                target = history["string"].get("key", "")
            elif "term" in history:
                target = history["term"].get("key", "")
            elif "file" in history:
                target = history["file"].get("name", "")

            target_item = QTableWidgetItem(target)
            self.history_table.setItem(row, 3, target_item)

            # 详情
            details = ""
            if "string" in history:
                details = f"原文: {history['string'].get('original', '')}"
            elif "term" in history:
                details = f"原文: {history['term'].get('original', '')}"

            details_item = QTableWidgetItem(details)
            self.history_table.setItem(row, 4, details_item)

            # 存储完整历史记录数据
            self.history_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, history)

    def update_page_controls(self):
        """更新分页控件"""
        self.page_input.setValue(self.current_page)
        self.page_input.setMaximum(self.total_pages)
        self.page_label.setText(f"/ {self.total_pages}")

    def on_page_changed(self, page):
        """页码变化处理"""
        if page != self.current_page:
            self.load_histories(page)

    def on_history_selection_changed(self):
        """历史记录选择变化处理"""
        selected_items = self.history_table.selectedItems()
        has_selection = len(selected_items) > 0

        if has_selection:
            # 获取历史记录数据
            history = selected_items[0].data(Qt.ItemDataRole.UserRole)
            self.current_history = history
            self.show_history_details(history)
            self.history_selected.emit(history)
        else:
            self.current_history = None
            self.history_info.setPlainText("请选择一条历史记录查看详情")
            self.diff_info.clear()

    def on_history_double_clicked(self, item):
        """历史记录双击处理"""
        history = item.data(Qt.ItemDataRole.UserRole)
        self.history_selected.emit(history)

    def show_history_details(self, history):
        """显示历史记录详情"""
        # 基本信息
        user = history.get("user", {})
        details = f"记录ID: {history.get('id', '')}"
        details += f"创建时间: {history.get('createdAt', '')}"
        details += f"用户: {user.get('username', '')} ({user.get('nickname', '')})"
        details += f"操作: {history.get('action', '')}"
        details += f"IP地址: {history.get('ip', '')}"

        self.history_info.setPlainText(details)

        # 变更对比
        diff_text = ""

        if "string" in history:
            string_data = history["string"]
            diff_text += f"词条ID: {string_data.get('id', '')}"
            diff_text += f"键名: {string_data.get('key', '')}"
            diff_text += f"原文: {string_data.get('original', '')}"
            diff_text += f"译文: {string_data.get('translation', '')}"

            if "previous" in history:
                prev = history["previous"]
                diff_text += "变更前:\n"
                diff_text += f"译文: {prev.get('translation', '')}"

        elif "term" in history:
            term_data = history["term"]
            diff_text += f"术语ID: {term_data.get('id', '')}"
            diff_text += f"键名: {term_data.get('key', '')}"
            diff_text += f"原文: {term_data.get('original', '')}"
            diff_text += f"译文: {term_data.get('translation', '')}"
            diff_text += f"描述: {term_data.get('description', '')}"

            if "previous" in history:
                prev = history["previous"]
                diff_text += "变更前:\n"
                diff_text += f"译文: {prev.get('translation', '')}"
                diff_text += f"描述: {prev.get('description', '')}"

        self.diff_info.setPlainText(diff_text)
