import json
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QDialog, QFormLayout, QMessageBox, QTabWidget, QTextEdit, QSplitter, QFrame, QSpinBox
)
from PyQt6.QtCore import pyqtSignal, Qt
from src.transbridge.paratranz.api.paratranz_terms_api import ParatranzTermsAPI


class TermCreateDialog(QDialog):
    """
    创建术语对话框
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("创建术语")
        self.resize(500, 400)
        
        self.init_ui()
    
    def init_ui(self):
        layout = QVBoxLayout()
        
        # 表单布局
        form_layout = QFormLayout()
        
        # 键名
        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("输入术语键名")
        form_layout.addRow("键名:", self.key_input)
        
        # 原文
        self.original_input = QLineEdit()
        self.original_input.setPlaceholderText("输入术语原文")
        form_layout.addRow("原文:", self.original_input)
        
        # 译文
        self.translation_input = QLineEdit()
        self.translation_input.setPlaceholderText("输入术语译文")
        form_layout.addRow("译文:", self.translation_input)
        
        # 描述
        self.description_input = QTextEdit()
        self.description_input.setPlaceholderText("输入术语描述（可选）")
        self.description_input.setMaximumHeight(100)
        form_layout.addRow("描述:", self.description_input)
        
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
    
    def get_term_data(self):
        """获取术语数据"""
        return {
            "key": self.key_input.text().strip(),
            "original": self.original_input.text().strip(),
            "translation": self.translation_input.text().strip(),
            "description": self.description_input.toPlainText().strip()
        }


class TermEditDialog(TermCreateDialog):
    """
    编辑术语对话框
    """
    
    def __init__(self, term_data, parent=None):
        self.term_data = term_data
        super().__init__(parent)
        self.setWindowTitle("编辑术语")
        self.load_term_data()
    
    def load_term_data(self):
        """加载术语数据到表单"""
        self.key_input.setText(self.term_data.get("key", ""))
        self.original_input.setText(self.term_data.get("original", ""))
        self.translation_input.setText(self.term_data.get("translation", ""))
        self.description_input.setPlainText(self.term_data.get("description", ""))


class BatchImportDialog(QDialog):
    """
    批量导入术语对话框
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量导入术语")
        self.resize(600, 500)
        
        self.init_ui()
    
    def init_ui(self):
        layout = QVBoxLayout()
        
        # 说明
        info_label = QLabel("输入JSON格式的术语列表，格式示例:")
        layout.addWidget(info_label)
        
        # 示例
        example_text = QTextEdit()
        example_text.setReadOnly(True)
        example_text.setMaximumHeight(120)
        example_text.setPlainText('''[
    {"key": "NPC", "original": "Non-player character", "translation": "非玩家角色"},
    {"key": "HP", "original": "Health Point", "translation": "生命值"}
]''')
        layout.addWidget(example_text)
        
        # 术语输入
        self.terms_input = QTextEdit()
        self.terms_input.setPlaceholderText("在此输入JSON格式的术语列表...")
        layout.addWidget(self.terms_input)
        
        # 按钮区域
        button_layout = QHBoxLayout()
        
        self.import_button = QPushButton("导入")
        self.import_button.clicked.connect(self.import_terms)
        
        button_layout.addWidget(self.import_button)
        button_layout.addStretch()
        
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_button)
        
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
    
    def import_terms(self):
        """导入术语"""
        terms_text = self.terms_input.toPlainText().strip()
        
        if not terms_text:
            QMessageBox.warning(self, "警告", "请输入术语列表")
            return
        
        try:
            terms = json.loads(terms_text)
            
            if not isinstance(terms, list):
                raise ValueError("输入内容必须是列表格式")
            
            for term in terms:
                if not isinstance(term, dict):
                    raise ValueError("每个术语必须是字典格式")
                
                if "key" not in term or "original" not in term or "translation" not in term:
                    raise ValueError("每个术语必须包含 key, original 和 translation 字段")
            
            self.terms = terms
            self.accept()
            
        except json.JSONDecodeError:
            QMessageBox.critical(self, "错误", "JSON格式不正确，请检查输入")
        except ValueError as e:
            QMessageBox.critical(self, "错误", str(e))
        except Exception as e:
            QMessageBox.critical(self, "错误", f"导入失败: {str(e)}")
    
    def get_terms(self):
        """获取导入的术语列表"""
        return getattr(self, "terms", [])


class TermsUI(QWidget):
    """
    Paratranz 术语管理界面
    """
    
    # 信号：术语被选中
    term_selected = pyqtSignal(dict)
    
    def __init__(self, client_config, parent=None):
        super().__init__(parent)
        self.client_config = client_config
        self.api = ParatranzTermsAPI(
            token=client_config.token,
            timeout=client_config.timeout,
            config=client_config
        )
        
        self.terms = []
        self.current_term = None
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
        self.refresh_button.clicked.connect(self.load_terms)
        
        self.create_button = QPushButton("创建术语")
        self.create_button.clicked.connect(self.create_term)
        
        self.edit_button = QPushButton("编辑术语")
        self.edit_button.clicked.connect(self.edit_term)
        self.edit_button.setEnabled(False)
        
        self.delete_button = QPushButton("删除术语")
        self.delete_button.clicked.connect(self.delete_term)
        self.delete_button.setEnabled(False)
        
        self.batch_import_button = QPushButton("批量导入")
        self.batch_import_button.clicked.connect(self.batch_import_terms)
        
        toolbar_layout.addWidget(self.refresh_button)
        toolbar_layout.addWidget(self.create_button)
        toolbar_layout.addWidget(self.edit_button)
        toolbar_layout.addWidget(self.delete_button)
        toolbar_layout.addWidget(self.batch_import_button)
        toolbar_layout.addStretch()
        
        main_layout.addLayout(toolbar_layout)
        
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
        
        # 左侧术语列表
        left_frame = QFrame()
        left_layout = QVBoxLayout()
        
        # 术语表格
        self.term_table = QTableWidget()
        self.term_table.setColumnCount(4)
        self.term_table.setHorizontalHeaderLabels(["ID", "键名", "原文", "译文"])
        self.term_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.term_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.term_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.term_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.term_table.itemSelectionChanged.connect(self.on_term_selection_changed)
        self.term_table.itemDoubleClicked.connect(self.on_term_double_clicked)
        
        left_layout.addWidget(self.term_table)
        left_frame.setLayout(left_layout)
        
        # 右侧术语详情
        right_frame = QFrame()
        right_layout = QVBoxLayout()
        
        self.term_details = QTabWidget()
        
        # 基本信息标签页
        self.info_tab = QWidget()
        info_layout = QVBoxLayout()
        
        self.term_info = QTextEdit()
        self.term_info.setReadOnly(True)
        self.term_info.setPlainText("请选择一个术语查看详情")
        
        info_layout.addWidget(self.term_info)
        self.info_tab.setLayout(info_layout)
        
        self.term_details.addTab(self.info_tab, "基本信息")
        
        right_layout.addWidget(self.term_details)
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
            self.load_terms()
    
    def load_terms(self, page=1):
        """加载术语列表"""
        if not self.project_id:
            return
        
        try:
            result = self.api.list_terms(self.project_id, page)
            self.terms = result.get("docs", [])
            self.total_pages = result.get("pages", 1)
            self.current_page = result.get("page", 1)
            
            self.update_term_table()
            self.update_page_controls()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载术语列表失败: {str(e)}")
    
    def update_term_table(self):
        """更新术语表格"""
        self.term_table.setRowCount(len(self.terms))
        
        for row, term in enumerate(self.terms):
            # 术语ID
            id_item = QTableWidgetItem(str(term.get("id", "")))
            self.term_table.setItem(row, 0, id_item)
            
            # 键名
            key_item = QTableWidgetItem(term.get("key", ""))
            self.term_table.setItem(row, 1, key_item)
            
            # 原文
            original_item = QTableWidgetItem(term.get("original", ""))
            self.term_table.setItem(row, 2, original_item)
            
            # 译文
            translation_item = QTableWidgetItem(term.get("translation", ""))
            self.term_table.setItem(row, 3, translation_item)
            
            # 存储完整术语数据
            self.term_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, term)
    
    def update_page_controls(self):
        """更新分页控件"""
        self.page_input.setValue(self.current_page)
        self.page_input.setMaximum(self.total_pages)
        self.page_label.setText(f"/ {self.total_pages}")
    
    def on_term_selection_changed(self):
        """术语选择变化处理"""
        selected_items = self.term_table.selectedItems()
        has_selection = len(selected_items) > 0
        
        self.edit_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)
        
        if has_selection:
            # 获取术语数据
            term = selected_items[0].data(Qt.ItemDataRole.UserRole)
            self.current_term = term
            self.show_term_details(term)
            self.term_selected.emit(term)
        else:
            self.current_term = None
            self.term_info.setPlainText("请选择一个术语查看详情")
    
    def on_term_double_clicked(self, item):
        """术语双击处理"""
        term = item.data(Qt.ItemDataRole.UserRole)
        self.term_selected.emit(term)
    
    def on_page_changed(self, page):
        """页码变化处理"""
        if page != self.current_page:
            self.load_terms(page)
    
    def show_term_details(self, term):
        """显示术语详情"""
        details = f"术语ID: {term.get('id', '')}\n"
        details += f"键名: {term.get('key', '')}\n"
        details += f"原文: {term.get('original', '')}\n"
        details += f"译文: {term.get('translation', '')}\n"
        details += f"描述: {term.get('description', '')}\n"
        details += f"创建时间: {term.get('createdAt', '')}\n"
        details += f"更新时间: {term.get('updatedAt', '')}\n"
        
        self.term_info.setPlainText(details)
    
    def create_term(self):
        """创建新术语"""
        if not self.project_id:
            QMessageBox.warning(self, "警告", "请先选择一个项目")
            return
        
        dialog = TermCreateDialog(self)
        if dialog.exec():
            term_data = dialog.get_term_data()
            try:
                new_term = self.api.create_term(self.project_id, term_data)
                QMessageBox.information(self, "成功", "术语创建成功!")
                self.load_terms()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"创建术语失败: {str(e)}")
    
    def edit_term(self):
        """编辑术语"""
        if not self.current_term:
            return
        
        dialog = TermEditDialog(self.current_term, self)
        if dialog.exec():
            term_data = dialog.get_term_data()
            try:
                self.api.update_term(self.project_id, self.current_term["id"], term_data)
                QMessageBox.information(self, "成功", "术语更新成功!")
                self.load_terms()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"更新术语失败: {str(e)}")
    
    def delete_term(self):
        """删除术语"""
        if not self.current_term:
            return
        
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除术语 '{self.current_term.get('key', '')}' 吗?\n此操作不可撤销!",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.api.delete_term(self.project_id, self.current_term["id"])
                QMessageBox.information(self, "成功", "术语删除成功!")
                self.load_terms()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"删除术语失败: {str(e)}")
    
    def batch_import_terms(self):
        """批量导入术语"""
        if not self.project_id:
            QMessageBox.warning(self, "警告", "请先选择一个项目")
            return
        
        dialog = BatchImportDialog(self)
        if dialog.exec():
            terms = dialog.get_terms()
            try:
                self.api.batch_import_terms(self.project_id, terms)
                QMessageBox.information(self, "成功", f"成功导入 {len(terms)} 个术语!")
                self.load_terms()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"批量导入术语失败: {str(e)}")