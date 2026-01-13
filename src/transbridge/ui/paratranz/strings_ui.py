from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QDialog, QFormLayout, QMessageBox, QComboBox, QGroupBox,
    QTabWidget, QTextEdit, QSplitter, QFrame, QSpinBox,
    QListWidget, QListWidgetItem
)
from PyQt6.QtCore import pyqtSignal, Qt
from src.transbridge.paratranz.api.paratranz_strings_api import ParatranzStringsAPI


class StringCreateDialog(QDialog):
    """
    创建词条对话框
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("创建词条")
        self.resize(500, 400)

        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # 表单布局
        form_layout = QFormLayout()

        # 键名
        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("输入词条键名")
        form_layout.addRow("键名:", self.key_input)

        # 原文
        self.original_input = QTextEdit()
        self.original_input.setPlaceholderText("输入原文内容")
        self.original_input.setMaximumHeight(80)
        form_layout.addRow("原文:", self.original_input)

        # 译文
        self.translation_input = QTextEdit()
        self.translation_input.setPlaceholderText("输入译文内容")
        self.translation_input.setMaximumHeight(80)
        form_layout.addRow("译文:", self.translation_input)

        # 上下文
        self.context_input = QLineEdit()
        self.context_input.setPlaceholderText("输入上下文信息（可选）")
        form_layout.addRow("上下文:", self.context_input)

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

    def get_string_data(self):
        """获取词条数据"""
        return {
            "key": self.key_input.text().strip(),
            "original": self.original_input.toPlainText().strip(),
            "translation": self.translation_input.toPlainText().strip(),
            "context": self.context_input.text().strip()
        }


class StringEditDialog(StringCreateDialog):
    """
    编辑词条对话框
    """

    def __init__(self, string_data, parent=None):
        self.string_data = string_data
        super().__init__(parent)
        self.setWindowTitle("编辑词条")
        self.load_string_data()

    def load_string_data(self):
        """加载词条数据到表单"""
        self.key_input.setText(self.string_data.get("key", ""))
        self.original_input.setPlainText(self.string_data.get("original", ""))
        self.translation_input.setPlainText(self.string_data.get("translation", ""))
        self.context_input.setText(self.string_data.get("context", ""))


class BatchUpdateDialog(QDialog):
    """
    批量更新词条对话框
    """

    def __init__(self, strings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量更新词条")
        self.resize(600, 500)

        self.strings = strings
        self.updates = []

        self.init_ui()
        self.load_strings()

    def init_ui(self):
        layout = QVBoxLayout()

        # 说明
        info_label = QLabel("选择要更新的词条并编辑译文内容:")
        layout.addWidget(info_label)

        # 词条列表
        self.strings_list = QListWidget()
        layout.addWidget(self.strings_list)

        # 编辑区域
        edit_group = QGroupBox("编辑选中的词条")
        edit_layout = QFormLayout()

        self.translation_input = QTextEdit()
        self.translation_input.setPlaceholderText("输入新的译文内容")
        self.translation_input.setMaximumHeight(100)
        edit_layout.addRow("译文:", self.translation_input)

        edit_group.setLayout(edit_layout)
        layout.addWidget(edit_group)

        # 按钮区域
        button_layout = QHBoxLayout()

        self.add_to_batch_button = QPushButton("添加到批量更新")
        self.add_to_batch_button.clicked.connect(self.add_to_batch)
        self.add_to_batch_button.setEnabled(False)

        self.execute_button = QPushButton("执行批量更新")
        self.execute_button.clicked.connect(self.execute_batch_update)

        button_layout.addWidget(self.add_to_batch_button)
        button_layout.addWidget(self.execute_button)
        button_layout.addStretch()

        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_button)

        layout.addLayout(button_layout)

        self.setLayout(layout)

        # 连接信号
        self.strings_list.itemSelectionChanged.connect(self.on_string_selection_changed)

    def load_strings(self):
        """加载词条到列表"""
        for string_data in self.strings:
            item = QListWidgetItem(f"{string_data.get('key', '')} - {string_data.get('original', '')}")
            item.setData(Qt.ItemDataRole.UserRole, string_data)
            self.strings_list.addItem(item)

    def on_string_selection_changed(self):
        """词条选择变化处理"""
        selected_items = self.strings_list.selectedItems()
        has_selection = len(selected_items) > 0

        self.add_to_batch_button.setEnabled(has_selection)

        if has_selection:
            string_data = selected_items[0].data(Qt.ItemDataRole.UserRole)
            self.translation_input.setPlainText(string_data.get("translation", ""))

    def add_to_batch(self):
        """添加到批量更新列表"""
        selected_items = self.strings_list.selectedItems()
        if not selected_items:
            return

        string_data = selected_items[0].data(Qt.ItemDataRole.UserRole)
        translation = self.translation_input.toPlainText().strip()

        if not translation:
            QMessageBox.warning(self, "警告", "译文内容不能为空")
            return

        # 添加到更新列表
        self.updates.append({
            "id": string_data["id"],
            "translation": translation
        })

        # 标记已添加
        item = selected_items[0]
        item.setText(f"[已添加] {item.text()}")
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)

        # 清空编辑区
        self.translation_input.clear()

        QMessageBox.information(self, "成功", f"已添加到批量更新列表 (共{len(self.updates)}项)")

    def execute_batch_update(self):
        """执行批量更新"""
        if not self.updates:
            QMessageBox.warning(self, "警告", "没有要更新的词条")
            return

        reply = QMessageBox.question(
            self, "确认批量更新",
            f"确定要批量更新 {len(self.updates)} 个词条吗?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.accept()

    def get_updates(self):
        """获取批量更新数据"""
        return self.updates


class StringsUI(QWidget):
    """
    Paratranz 词条管理界面
    """

    # 信号：词条被选中
    string_selected = pyqtSignal(dict)

    def __init__(self, client_config, parent=None):
        super().__init__(parent)
        self.client_config = client_config
        self.api = ParatranzStringsAPI(
            token=client_config.token,
            timeout=client_config.timeout,
            config=client_config
        )

        self.strings = []
        self.current_string = None
        self.project_id = None
        self.current_page = 1
        self.total_pages = 1

        self.init_ui()

    def init_ui(self):
        # 主布局
        main_layout = QVBoxLayout()

        # 顶部工具栏
        toolbar_layout = QHBoxLayout()

        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.load_strings)

        self.create_button = QPushButton("创建词条")
        self.create_button.clicked.connect(self.create_string)

        self.edit_button = QPushButton("编辑词条")
        self.edit_button.clicked.connect(self.edit_string)
        self.edit_button.setEnabled(False)

        self.delete_button = QPushButton("删除词条")
        self.delete_button.clicked.connect(self.delete_string)
        self.delete_button.setEnabled(False)

        self.batch_update_button = QPushButton("批量更新")
        self.batch_update_button.clicked.connect(self.batch_update_strings)

        self.batch_delete_button = QPushButton("批量删除")
        self.batch_delete_button.clicked.connect(self.batch_delete_strings)

        toolbar_layout.addWidget(self.refresh_button)
        toolbar_layout.addWidget(self.create_button)
        toolbar_layout.addWidget(self.edit_button)
        toolbar_layout.addWidget(self.delete_button)
        toolbar_layout.addWidget(self.batch_update_button)
        toolbar_layout.addWidget(self.batch_delete_button)
        toolbar_layout.addStretch()

        main_layout.addLayout(toolbar_layout)

        # 筛选区域
        filter_layout = QHBoxLayout()

        filter_layout.addWidget(QLabel("语言:"))
        self.lang_filter = QComboBox()
        self.lang_filter.setEditable(True)
        self.lang_filter.addItem("")
        self.lang_filter.addItems([
            "en", "zh-CN", "zh-TW", "ja", "ko", "fr", "de", "es", "ru", "pt"
        ])
        self.lang_filter.currentTextChanged.connect(self.on_filter_changed)
        filter_layout.addWidget(self.lang_filter)

        filter_layout.addWidget(QLabel("页码:"))
        self.page_input = QSpinBox()
        self.page_input.setMinimum(1)
        self.page_input.valueChanged.connect(self.on_page_changed)
        filter_layout.addWidget(self.page_input)

        self.page_label = QLabel("/ 1")
        filter_layout.addWidget(self.page_label)

        filter_layout.addStretch()

        main_layout.addLayout(filter_layout)

        # 分割器
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧词条列表
        left_frame = QFrame()
        left_layout = QVBoxLayout()

        # 词条表格
        self.string_table = QTableWidget()
        self.string_table.setColumnCount(5)
        self.string_table.setHorizontalHeaderLabels(["ID", "键名", "原文", "译文", "上下文"])
        self.string_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.string_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.string_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.string_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.string_table.itemSelectionChanged.connect(self.on_string_selection_changed)
        self.string_table.itemDoubleClicked.connect(self.on_string_double_clicked)

        left_layout.addWidget(self.string_table)
        left_frame.setLayout(left_layout)

        # 右侧词条详情
        right_frame = QFrame()
        right_layout = QVBoxLayout()

        self.string_details = QTabWidget()

        # 基本信息标签页
        self.info_tab = QWidget()
        info_layout = QVBoxLayout()

        self.string_info = QTextEdit()
        self.string_info.setReadOnly(True)
        self.string_info.setPlainText("请选择一个词条查看详情")

        info_layout.addWidget(self.string_info)
        self.info_tab.setLayout(info_layout)

        self.string_details.addTab(self.info_tab, "基本信息")

        right_layout.addWidget(self.string_details)
        right_frame.setLayout(right_layout)

        splitter.addWidget(left_frame)
        splitter.addWidget(right_frame)
        splitter.setSizes([600, 400])

        main_layout.addWidget(splitter)

        self.setLayout(main_layout)

    def set_project_id(self, project_id):
        """设置项目ID"""
        self.project_id = project_id
        self.load_strings()

    def load_strings(self):
        """加载词条列表"""
        if not self.project_id:
            return

        try:
            lang = self.lang_filter.currentText()
            lang = lang if lang else None

            result = self.api.list_strings(self.project_id, self.current_page, lang)
            self.strings = result.get("docs", [])
            self.total_pages = result.get("pages", 1)

            self.update_string_table()
            self.update_page_info()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载词条列表失败: {str(e)}")

    def update_string_table(self):
        """更新词条表格"""
        self.string_table.setRowCount(len(self.strings))

        for row, string_data in enumerate(self.strings):
            # 词条ID
            id_item = QTableWidgetItem(str(string_data.get("id", "")))
            self.string_table.setItem(row, 0, id_item)

            # 键名
            key_item = QTableWidgetItem(string_data.get("key", ""))
            self.string_table.setItem(row, 1, key_item)

            # 原文
            original_item = QTableWidgetItem(string_data.get("original", ""))
            self.string_table.setItem(row, 2, original_item)

            # 译文
            translation_item = QTableWidgetItem(string_data.get("translation", ""))
            self.string_table.setItem(row, 3, translation_item)

            # 上下文
            context_item = QTableWidgetItem(string_data.get("context", ""))
            self.string_table.setItem(row, 4, context_item)

            # 存储完整词条数据
            self.string_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, string_data)

    def update_page_info(self):
        """更新页面信息"""
        self.page_input.setMaximum(self.total_pages)
        self.page_label.setText(f"/ {self.total_pages}")

    def on_filter_changed(self):
        """筛选条件变化处理"""
        self.current_page = 1
        self.page_input.setValue(1)
        self.load_strings()

    def on_page_changed(self, page):
        """页码变化处理"""
        if page != self.current_page:
            self.current_page = page
            self.load_strings()

    def on_string_selection_changed(self):
        """词条选择变化处理"""
        selected_items = self.string_table.selectedItems()
        has_selection = len(selected_items) > 0

        self.edit_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)

        if has_selection:
            # 获取词条数据
            string_data = selected_items[0].data(Qt.ItemDataRole.UserRole)
            self.current_string = string_data
            self.show_string_details(string_data)
            self.string_selected.emit(string_data)
        else:
            self.current_string = None
            self.string_info.setPlainText("请选择一个词条查看详情")

    def on_string_double_clicked(self, item):
        """词条双击处理"""
        string_data = item.data(Qt.ItemDataRole.UserRole)
        self.string_selected.emit(string_data)

    def show_string_details(self, string_data):
        """显示词条详情"""
        details = f"词条ID: {string_data.get('id', '')}"
        details += f"键名: {string_data.get('key', '')}"
        details += f"原文: {string_data.get('original', '')}"
        details += f"译文: {string_data.get('translation', '')}"
        details += f"上下文: {string_data.get('context', '')}"
        details += f"创建时间: {string_data.get('createdAt', '')}"
        details += f"更新时间: {string_data.get('updatedAt', '')}"

        self.string_info.setPlainText(details)

    def create_string(self):
        """创建词条"""
        dialog = StringCreateDialog(self)
        if dialog.exec():
            string_data = dialog.get_string_data()
            try:
                self.api.create_string(self.project_id, string_data)
                QMessageBox.information(self, "成功", "词条创建成功!")
                self.load_strings()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"创建词条失败: {str(e)}")

    def edit_string(self):
        """编辑词条"""
        if not self.current_string:
            return

        dialog = StringEditDialog(self.current_string, self)
        if dialog.exec():
            string_data = dialog.get_string_data()
            try:
                self.api.update_string(self.project_id, self.current_string["id"], string_data)
                QMessageBox.information(self, "成功", "词条更新成功!")
                self.load_strings()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"更新词条失败: {str(e)}")

    def delete_string(self):
        """删除词条"""
        if not self.current_string:
            return

        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除词条 '{self.current_string.get('key', '')}' 吗?\n此操作不可撤销!",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.api.delete_string(self.project_id, self.current_string["id"])
                QMessageBox.information(self, "成功", "词条删除成功!")
                self.load_strings()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"删除词条失败: {str(e)}")

    def batch_update_strings(self):
        """批量更新词条"""
        if not self.strings:
            QMessageBox.information(self, "提示", "没有可更新的词条")
            return

        dialog = BatchUpdateDialog(self.strings, self)
        if dialog.exec():
            updates = dialog.get_updates()
            try:
                self.api.batch_update(self.project_id, updates)
                QMessageBox.information(self, "成功", f"批量更新 {len(updates)} 个词条成功!")
                self.load_strings()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"批量更新词条失败: {str(e)}")

    def batch_delete_strings(self):
        """批量删除词条"""
        if not self.strings:
            QMessageBox.information(self, "提示", "没有可删除的词条")
            return

        # 获取选中的词条
        selected_items = self.string_table.selectedItems()
        if not selected_items:
            QMessageBox.information(self, "提示", "请先选择要删除的词条")
            return

        # 获取所有选中词条的ID
        selected_rows = set()
        for item in selected_items:
            selected_rows.add(item.row())

        string_ids = []
        for row in selected_rows:
            string_data = self.string_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            string_ids.append(string_data["id"])

        reply = QMessageBox.question(
            self, "确认批量删除",
            f"确定要批量删除 {len(string_ids)} 个词条吗?\n此操作不可撤销!",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.api.batch_delete(self.project_id, string_ids)
                QMessageBox.information(self, "成功", f"批量删除 {len(string_ids)} 个词条成功!")
                self.load_strings()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"批量删除词条失败: {str(e)}")
