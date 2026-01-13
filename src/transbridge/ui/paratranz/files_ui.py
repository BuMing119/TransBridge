import json
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QDialog, QFormLayout, QMessageBox, QComboBox, QTabWidget, QTextEdit, QSplitter, QFrame, QFileDialog,
    QProgressBar, QCheckBox
)
from PyQt6.QtCore import pyqtSignal, Qt, QThread, pyqtSlot
from src.transbridge.paratranz.api.paratranz_files_api import ParatranzFilesAPI


class FileUploadDialog(QDialog):
    """
    文件上传对话框
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("上传文件")
        self.resize(500, 300)

        self.file_path = ""
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # 文件选择
        file_layout = QHBoxLayout()
        self.file_path_input = QLineEdit()
        self.file_path_input.setReadOnly(True)
        self.file_path_input.setPlaceholderText("选择要上传的文件")

        self.browse_button = QPushButton("浏览...")
        self.browse_button.clicked.connect(self.browse_file)

        file_layout.addWidget(self.file_path_input)
        file_layout.addWidget(self.browse_button)
        layout.addLayout(file_layout)

        # 上传选项
        options_layout = QFormLayout()

        # 语言
        self.lang_input = QComboBox()
        self.lang_input.setEditable(True)
        self.lang_input.addItems([
            "en", "zh-CN", "zh-TW", "ja", "ko", "fr", "de", "es", "ru", "pt"
        ])
        options_layout.addRow("目标语言:", self.lang_input)

        # 保持路径
        self.keep_path_input = QCheckBox("保持文件路径结构")
        self.keep_path_input.setChecked(True)
        options_layout.addRow(self.keep_path_input)

        layout.addLayout(options_layout)

        # 按钮区域
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.upload_button = QPushButton("上传")
        self.upload_button.clicked.connect(self.accept)
        self.upload_button.setEnabled(False)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.reject)

        button_layout.addWidget(self.upload_button)
        button_layout.addWidget(self.cancel_button)

        layout.addLayout(button_layout)

        self.setLayout(layout)

    def browse_file(self):
        """浏览文件"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择文件", "", "所有文件 (*);;JSON 文件 (*.json);;文本文件 (*.txt)"
        )

        if file_path:
            self.file_path = file_path
            self.file_path_input.setText(file_path)
            self.upload_button.setEnabled(True)

    def get_upload_options(self):
        """获取上传选项"""
        return {
            "file_path": self.file_path,
            "lang": self.lang_input.currentText(),
            "keepPath": self.keep_path_input.isChecked()
        }


class FileEditDialog(QDialog):
    """
    文件编辑对话框
    """

    def __init__(self, file_data, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑文件信息")
        self.resize(400, 200)

        self.file_data = file_data
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # 表单布局
        form_layout = QFormLayout()

        # 文件名
        self.name_input = QLineEdit()
        self.name_input.setText(self.file_data.get("name", ""))
        form_layout.addRow("文件名:", self.name_input)

        # 语言
        self.lang_input = QComboBox()
        self.lang_input.setEditable(True)
        self.lang_input.addItems([
            "en", "zh-CN", "zh-TW", "ja", "ko", "fr", "de", "es", "ru", "pt"
        ])
        self.lang_input.setCurrentText(self.file_data.get("lang", ""))
        form_layout.addRow("语言:", self.lang_input)

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

    def get_file_data(self):
        """获取文件数据"""
        return {
            "name": self.name_input.text().strip(),
            "lang": self.lang_input.currentText()
        }


class FileTranslationDialog(QDialog):
    """
    文件翻译内容编辑对话框
    """

    def __init__(self, file_data, lang, client_config, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"编辑翻译内容 - {lang}")
        self.resize(800, 600)

        self.file_data = file_data
        self.lang = lang
        self.client_config = client_config
        self.api = ParatranzFilesAPI(
            token=client_config["token"],
            timeout=client_config["timeout"]
        )

        self.translation_content = {}

        self.init_ui()
        self.load_translation()

    def init_ui(self):
        layout = QVBoxLayout()

        # 顶部信息
        info_layout = QHBoxLayout()
        info_layout.addWidget(QLabel(f"文件: {self.file_data.get('name', '')}"))
        info_layout.addWidget(QLabel(f"语言: {self.lang}"))
        info_layout.addStretch()

        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.load_translation)
        info_layout.addWidget(self.refresh_button)

        layout.addLayout(info_layout)

        # 翻译内容编辑器
        self.translation_editor = QTextEdit()
        self.translation_editor.setPlaceholderText("翻译内容将在此显示...")
        layout.addWidget(self.translation_editor)

        # 底部按钮
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.save_button = QPushButton("保存")
        self.save_button.clicked.connect(self.save_translation)
        self.cancel_button = QPushButton("关闭")
        self.cancel_button.clicked.connect(self.reject)

        button_layout.addWidget(self.save_button)
        button_layout.addWidget(self.cancel_button)

        layout.addLayout(button_layout)

        self.setLayout(layout)

    def load_translation(self):
        """加载翻译内容"""
        try:
            self.translation_content = self.api.get_file_translation(
                self.file_data["projectId"],
                self.file_data["id"],
                self.lang
            )
            self.translation_editor.setPlainText(json.dumps(self.translation_content, indent=2, ensure_ascii=False))
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载翻译内容失败: {str(e)}")

    def save_translation(self):
        """保存翻译内容"""
        try:
            content_text = self.translation_editor.toPlainText()
            if not content_text.strip():
                QMessageBox.warning(self, "警告", "翻译内容不能为空")
                return

            self.translation_content = json.loads(content_text)
            self.api.update_file_translation(
                self.file_data["projectId"],
                self.file_data["id"],
                self.lang,
                self.translation_content
            )
            QMessageBox.information(self, "成功", "翻译内容保存成功!")
        except json.JSONDecodeError:
            QMessageBox.critical(self, "错误", "翻译内容格式不正确，请检查JSON格式")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存翻译内容失败: {str(e)}")


class FileUploadThread(QThread):
    """
    文件上传线程
    """

    progress_updated = pyqtSignal(int)
    upload_finished = pyqtSignal(bool, str)

    def __init__(self, api, project_id, upload_options):
        super().__init__()
        self.api = api
        self.project_id = project_id
        self.upload_options = upload_options

    def run(self):
        try:
            # 模拟进度更新
            self.progress_updated.emit(10)

            # 执行上传
            result = self.api.upload_file(
                self.project_id,
                self.upload_options["file_path"],
                lang=self.upload_options["lang"],
                keepPath=self.upload_options["keepPath"]
            )

            self.progress_updated.emit(100)
            self.upload_finished.emit(True, "文件上传成功!")
        except Exception as e:
            self.upload_finished.emit(False, f"文件上传失败: {str(e)}")


class FilesUI(QWidget):
    """
    Paratranz 文件管理界面
    """

    # 信号：文件被选中
    file_selected = pyqtSignal(dict)

    def __init__(self, client_config, parent=None):
        super().__init__(parent)
        self.client_config = client_config
        self.api = ParatranzFilesAPI(
            token=client_config.token,
            timeout=client_config.timeout,
            config=client_config
        )

        self.files = []
        self.current_file = None
        self.project_id = None

        self.init_ui()

    def init_ui(self):
        # 主布局
        main_layout = QVBoxLayout()

        # 顶部工具栏
        toolbar_layout = QHBoxLayout()

        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.load_files)

        self.upload_button = QPushButton("上传文件")
        self.upload_button.clicked.connect(self.upload_file)

        self.edit_button = QPushButton("编辑文件")
        self.edit_button.clicked.connect(self.edit_file)
        self.edit_button.setEnabled(False)

        self.delete_button = QPushButton("删除文件")
        self.delete_button.clicked.connect(self.delete_file)
        self.delete_button.setEnabled(False)

        self.download_button = QPushButton("下载翻译")
        self.download_button.clicked.connect(self.download_translation)
        self.download_button.setEnabled(False)

        toolbar_layout.addWidget(self.refresh_button)
        toolbar_layout.addWidget(self.upload_button)
        toolbar_layout.addWidget(self.edit_button)
        toolbar_layout.addWidget(self.delete_button)
        toolbar_layout.addWidget(self.download_button)
        toolbar_layout.addStretch()

        main_layout.addLayout(toolbar_layout)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)

        # 分割器
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧文件列表
        left_frame = QFrame()
        left_layout = QVBoxLayout()

        # 文件表格
        self.file_table = QTableWidget()
        self.file_table.setColumnCount(4)
        self.file_table.setHorizontalHeaderLabels(["ID", "文件名", "语言", "状态"])
        self.file_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.file_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.file_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.file_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.file_table.itemSelectionChanged.connect(self.on_file_selection_changed)
        self.file_table.itemDoubleClicked.connect(self.on_file_double_clicked)

        left_layout.addWidget(self.file_table)
        left_frame.setLayout(left_layout)

        # 右侧文件详情
        right_frame = QFrame()
        right_layout = QVBoxLayout()

        self.file_details = QTabWidget()

        # 基本信息标签页
        self.info_tab = QWidget()
        info_layout = QVBoxLayout()

        self.file_info = QTextEdit()
        self.file_info.setReadOnly(True)
        self.file_info.setPlainText("请选择一个文件查看详情")

        info_layout.addWidget(self.file_info)
        self.info_tab.setLayout(info_layout)

        self.file_details.addTab(self.info_tab, "基本信息")

        right_layout.addWidget(self.file_details)
        right_frame.setLayout(right_layout)

        splitter.addWidget(left_frame)
        splitter.addWidget(right_frame)
        splitter.setSizes([300, 500])

        main_layout.addWidget(splitter)

        self.setLayout(main_layout)

    def set_project_id(self, project_id):
        """设置当前项目ID"""
        self.project_id = project_id
        if project_id:
            self.load_files()

    def load_files(self):
        """加载文件列表"""
        if not self.project_id:
            QMessageBox.warning(self, "警告", "请先选择一个项目")
            return

        try:
            self.files = self.api.list_files(self.project_id)
            self.update_file_table()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载文件列表失败: {str(e)}")

    def update_file_table(self):
        """更新文件表格"""
        self.file_table.setRowCount(len(self.files))

        for row, file in enumerate(self.files):
            # 文件ID
            id_item = QTableWidgetItem(str(file.get("id", "")))
            self.file_table.setItem(row, 0, id_item)

            # 文件名
            name_item = QTableWidgetItem(file.get("name", ""))
            self.file_table.setItem(row, 1, name_item)

            # 语言
            lang_item = QTableWidgetItem(file.get("lang", ""))
            self.file_table.setItem(row, 2, lang_item)

            # 状态
            status_item = QTableWidgetItem(file.get("status", ""))
            self.file_table.setItem(row, 3, status_item)

            # 存储完整文件数据
            self.file_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, file)

    def on_file_selection_changed(self):
        """文件选择变化处理"""
        selected_items = self.file_table.selectedItems()
        has_selection = len(selected_items) > 0

        self.edit_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)
        self.download_button.setEnabled(has_selection)

        if has_selection:
            # 获取文件数据
            file = selected_items[0].data(Qt.ItemDataRole.UserRole)
            self.current_file = file
            self.show_file_details(file)
            self.file_selected.emit(file)
        else:
            self.current_file = None
            self.file_info.setPlainText("请选择一个文件查看详情")

    def on_file_double_clicked(self, item):
        """文件双击处理"""
        file = item.data(Qt.ItemDataRole.UserRole)
        self.file_selected.emit(file)

    def show_file_details(self, file):
        """显示文件详情"""
        # 添加项目ID，因为API需要它
        file["projectId"] = self.project_id

        details = f"文件ID: {file.get('id', '')}"
        details += f"文件名: {file.get('name', '')}"
        details += f"语言: {file.get('lang', '')}"
        details += f"状态: {file.get('status', '')}"
        details += f"创建时间: {file.get('createdAt', '')}"
        details += f"更新时间: {file.get('updatedAt', '')}"

        self.file_info.setPlainText(details)

    def upload_file(self):
        """上传文件"""
        if not self.project_id:
            QMessageBox.warning(self, "警告", "请先选择一个项目")
            return

        dialog = FileUploadDialog(self)
        if dialog.exec():
            upload_options = dialog.get_upload_options()

            # 显示进度条
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(0)

            # 创建上传线程
            self.upload_thread = FileUploadThread(self.api, self.project_id, upload_options)
            self.upload_thread.progress_updated.connect(self.progress_bar.setValue)
            self.upload_thread.upload_finished.connect(self.on_upload_finished)
            self.upload_thread.start()

    @pyqtSlot(bool, str)
    def on_upload_finished(self, success, message):
        """上传完成处理"""
        self.progress_bar.setVisible(False)

        if success:
            QMessageBox.information(self, "成功", message)
            self.load_files()
        else:
            QMessageBox.critical(self, "错误", message)

    def edit_file(self):
        """编辑文件"""
        if not self.current_file:
            return

        dialog = FileEditDialog(self.current_file, self)
        if dialog.exec():
            file_data = dialog.get_file_data()
            try:
                self.api.update_file_info(self.project_id, self.current_file["id"], file_data)
                QMessageBox.information(self, "成功", "文件信息更新成功!")
                self.load_files()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"更新文件信息失败: {str(e)}")

    def delete_file(self):
        """删除文件"""
        if not self.current_file:
            return

        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除文件 '{self.current_file.get('name', '')}' 吗?\n此操作不可撤销!",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.api.delete_file(self.project_id, self.current_file["id"])
                QMessageBox.information(self, "成功", "文件删除成功!")
                self.load_files()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"删除文件失败: {str(e)}")

    def download_translation(self):
        """下载翻译内容"""
        if not self.current_file:
            return

        # 获取目标语言
        lang, ok = QComboBox.getItem(
            self, "选择语言", "请选择要下载的语言:",
            ["zh-CN", "zh-TW", "ja", "ko", "fr", "de", "es", "ru", "pt"]
        )

        if ok and lang:
            # 添加项目ID，因为API需要它
            self.current_file["projectId"] = self.project_id

            # 打开翻译编辑对话框
            dialog = FileTranslationDialog(self.current_file, lang, self.client_config, self)
            dialog.exec()
