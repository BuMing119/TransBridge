import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QDialog, QFormLayout, QMessageBox, QComboBox, QGroupBox,
    QFileDialog,
    QProgressBar
)
from PyQt6.QtCore import pyqtSignal, QThread, pyqtSlot
from src.transbridge.paratranz.api.paratranz_export_api import ParatranzExportAPI


class ExportDialog(QDialog):
    """
    导出对话框
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导出翻译")
        self.resize(400, 200)

        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # 表单布局
        form_layout = QFormLayout()

        # 语言选择
        self.lang_input = QComboBox()
        self.lang_input.setEditable(True)
        self.lang_input.addItems([
            "", "zh-CN", "zh-TW", "ja", "ko", "fr", "de", "es", "ru", "pt"
        ])
        self.lang_input.setCurrentText("")
        form_layout.addRow("目标语言 (留空导出所有语言):", self.lang_input)

        layout.addLayout(form_layout)

        # 按钮区域
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.export_button = QPushButton("开始导出")
        self.export_button.clicked.connect(self.accept)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.reject)

        button_layout.addWidget(self.export_button)
        button_layout.addWidget(self.cancel_button)

        layout.addLayout(button_layout)

        self.setLayout(layout)

    def get_export_options(self):
        """获取导出选项"""
        lang = self.lang_input.currentText().strip()
        return {
            "lang": lang if lang else None
        }


class ExportThread(QThread):
    """
    导出线程
    """

    progress_updated = pyqtSignal(int, str)
    export_finished = pyqtSignal(bool, str)

    def __init__(self, api, project_id, export_options):
        super().__init__()
        self.api = api
        self.project_id = project_id
        self.export_options = export_options

    def run(self):
        try:
            # 触发导出任务
            self.progress_updated.emit(10, "正在触发导出任务...")
            result = self.api.trigger_export(
                self.project_id,
                self.export_options.get("lang")
            )

            task_id = result.get("taskId")
            if not task_id:
                self.export_finished.emit(False, "导出任务创建失败")
                return

            # 轮询导出状态
            self.progress_updated.emit(30, "正在查询导出状态...")
            while True:
                result = self.api.get_export_result(self.project_id, task_id)
                status = result.get("status")

                if status == "completed":
                    self.progress_updated.emit(90, "导出完成，准备下载...")
                    break
                elif status == "failed":
                    self.export_finished.emit(False, "导出失败")
                    return

                progress = result.get("progress", 0)
                self.progress_updated.emit(30 + int(progress * 0.5), f"正在导出... {progress}%")

                # 等待一段时间再查询
                self.msleep(1000)

            self.export_finished.emit(True, result.get("url"))

        except Exception as e:
            self.export_finished.emit(False, f"导出过程中出错: {str(e)}")


class ExportUI(QWidget):
    """
    Paratranz 导出管理界面
    """

    def __init__(self, client_config, parent=None):
        super().__init__(parent)
        self.client_config = client_config
        self.api = ParatranzExportAPI(
            token=client_config.token,
            timeout=client_config.timeout,
            config=client_config
        )

        self.project_id = None
        self.export_thread = None

        self.init_ui()

    def init_ui(self):
        # 主布局
        main_layout = QVBoxLayout()

        # 顶部工具栏
        toolbar_layout = QHBoxLayout()

        self.export_button = QPushButton("导出翻译")
        self.export_button.clicked.connect(self.start_export)

        toolbar_layout.addWidget(self.export_button)
        toolbar_layout.addStretch()

        main_layout.addLayout(toolbar_layout)

        # 导出区域
        export_group = QGroupBox("导出设置")
        export_layout = QVBoxLayout()

        # 导出选项
        options_layout = QFormLayout()

        # 语言选择
        self.lang_input = QComboBox()
        self.lang_input.setEditable(True)
        self.lang_input.addItems([
            "", "zh-CN", "zh-TW", "ja", "ko", "fr", "de", "es", "ru", "pt"
        ])
        self.lang_input.setCurrentText("")
        options_layout.addRow("目标语言 (留空导出所有语言):", self.lang_input)

        export_layout.addLayout(options_layout)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        export_layout.addWidget(self.progress_bar)

        # 状态标签
        self.status_label = QLabel("准备就绪")
        export_layout.addWidget(self.status_label)

        export_group.setLayout(export_layout)
        main_layout.addWidget(export_group)

        # 导出历史
        history_group = QGroupBox("导出历史")
        history_layout = QVBoxLayout()

        # 历史表格
        self.history_table = QTableWidget()
        self.history_table.setColumnCount(4)
        self.history_table.setHorizontalHeaderLabels(["时间", "语言", "状态", "下载链接"])
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.history_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_table.itemDoubleClicked.connect(self.on_history_item_double_clicked)

        history_layout.addWidget(self.history_table)
        history_group.setLayout(history_layout)
        main_layout.addWidget(history_group)

        self.setLayout(main_layout)

    def set_project_id(self, project_id):
        """设置项目ID"""
        self.project_id = project_id

    def start_export(self):
        """开始导出"""
        if not self.project_id:
            QMessageBox.warning(self, "警告", "请先选择一个项目")
            return

        # 获取导出选项
        lang = self.lang_input.currentText().strip()
        export_options = {
            "lang": lang if lang else None
        }

        # 确认对话框
        lang_text = f" ({lang})" if lang else " (所有语言)"
        reply = QMessageBox.question(
            self, "确认导出",
            f"确定要导出翻译{lang_text}吗?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        # 创建并启动导出线程
        self.export_thread = ExportThread(self.api, self.project_id, export_options)
        self.export_thread.progress_updated.connect(self.on_progress_updated)
        self.export_thread.export_finished.connect(self.on_export_finished)

        # 显示进度条
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.status_label.setText("正在导出...")
        self.export_button.setEnabled(False)

        self.export_thread.start()

    @pyqtSlot(int, str)
    def on_progress_updated(self, value, message):
        """进度更新处理"""
        self.progress_bar.setValue(value)
        self.status_label.setText(message)

    @pyqtSlot(bool, str)
    def on_export_finished(self, success, result):
        """导出完成处理"""
        self.progress_bar.setVisible(False)
        self.export_button.setEnabled(True)

        if success:
            # 下载文件
            download_url = result
            if download_url:
                save_path, _ = QFileDialog.getSaveFileName(
                    self, "保存导出文件", 
                    os.path.join(os.path.expanduser("~"), "export.zip"),
                    "ZIP 文件 (*.zip)"
                )

                if save_path:
                    try:
                        self.status_label.setText("正在下载...")
                        self.api.download_export(download_url, save_path)
                        QMessageBox.information(self, "成功", f"导出文件已保存到: {save_path}")

                        # 添加到历史记录
                        self.add_to_history(download_url)
                    except Exception as e:
                        QMessageBox.critical(self, "错误", f"下载文件失败: {str(e)}")

            self.status_label.setText("导出完成")
        else:
            QMessageBox.critical(self, "错误", f"导出失败: {result}")
            self.status_label.setText("导出失败")

    def add_to_history(self, download_url):
        """添加到历史记录"""
        # 获取当前行数
        row_count = self.history_table.rowCount()

        # 添加新行
        self.history_table.insertRow(row_count)

        # 时间
        from datetime import datetime
        time_item = QTableWidgetItem(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self.history_table.setItem(row_count, 0, time_item)

        # 语言
        lang_text = self.lang_input.currentText().strip()
        lang_item = QTableWidgetItem(lang_text if lang_text else "所有语言")
        self.history_table.setItem(row_count, 1, lang_item)

        # 状态
        status_item = QTableWidgetItem("已完成")
        self.history_table.setItem(row_count, 2, status_item)

        # 下载链接
        url_item = QTableWidgetItem(download_url)
        self.history_table.setItem(row_count, 3, url_item)

        # 滚动到最新项
        self.history_table.scrollToBottom()

    def on_history_item_double_clicked(self, item):
        """历史项双击处理"""
        row = item.row()
        url_item = self.history_table.item(row, 3)
        download_url = url_item.text()

        if not download_url:
            return

        # 下载文件
        save_path, _ = QFileDialog.getSaveFileName(
            self, "保存导出文件", 
            os.path.join(os.path.expanduser("~"), f"export_{row}.zip"),
            "ZIP 文件 (*.zip)"
        )

        if save_path:
            try:
                self.api.download_export(download_url, save_path)
                QMessageBox.information(self, "成功", f"导出文件已保存到: {save_path}")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"下载文件失败: {str(e)}")
