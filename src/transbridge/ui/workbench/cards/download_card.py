from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QVBoxLayout, QHBoxLayout,
    QWidget, QLabel, QPushButton, QListWidget, QListWidgetItem,
    QStackedWidget, QMessageBox,
)
from PyQt6.QtCore import Qt

from src.transbridge.paratranz.workflow.downloader import ParaTranzDownloader
from ...workers import ApiWorker
from .base import OpCard


class _FileSelectDialog(QDialog):
    """下载合并文件选择对话框：从项目文件列表中勾选要参与合并的文件。"""

    def __init__(self, config, project_id: int, parent=None):
        super().__init__(parent)
        self._config = config
        self._project_id = project_id
        self._workers = []
        self._file_data: list[dict] = []

        self.setWindowTitle("选择要合并的文件")
        self.setMinimumSize(520, 380)

        layout = QVBoxLayout(self)

        tool_row = QHBoxLayout()
        self._btn_all = QPushButton("全选")
        self._btn_none = QPushButton("全不选")
        self._btn_all.setEnabled(False)
        self._btn_none.setEnabled(False)
        self._btn_all.clicked.connect(self._select_all)
        self._btn_none.clicked.connect(self._select_none)
        tool_row.addWidget(self._btn_all)
        tool_row.addWidget(self._btn_none)
        tool_row.addStretch()
        layout.addLayout(tool_row)

        self._stack = QStackedWidget()

        loading_widget = QWidget()
        loading_layout = QVBoxLayout(loading_widget)
        loading_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label = QLabel("正在获取文件列表…")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading_layout.addWidget(self._loading_label)
        self._stack.addWidget(loading_widget)  # index 0

        self._list_widget = QListWidget()
        self._list_widget.itemChanged.connect(self._on_item_changed)
        self._stack.addWidget(self._list_widget)  # index 1

        layout.addWidget(self._stack)

        self._status_label = QLabel("")
        layout.addWidget(self._status_label)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok_btn = btn_box.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setText("确认合并")
        self._ok_btn.setEnabled(False)
        btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self._load_files()

    def _load_files(self):
        from src.transbridge.paratranz.api.paratranz_files_api import ParatranzFilesAPI
        config = self._config
        project_id = self._project_id

        def _fetch():
            api = ParatranzFilesAPI(token=config.token, config=config)
            return api.list_files(project_id) or []

        w = ApiWorker(_fetch)
        w.result.connect(self._on_files_loaded)
        w.error.connect(self._on_load_error)
        w.start()
        self._workers.append(w)

    def _on_files_loaded(self, files: list):
        self._file_data = files
        self._list_widget.blockSignals(True)
        for f in files:
            name = f.get("name", str(f.get("id", "")))
            total = f.get("total", 0)
            translated = f.get("translated", 0)
            label = f"{name}    {total} 条  已译 {translated} 条"
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, f["id"])
            self._list_widget.addItem(item)
        self._list_widget.blockSignals(False)
        self._stack.setCurrentIndex(1)
        self._btn_all.setEnabled(True)
        self._btn_none.setEnabled(True)
        self._update_status()

    def _on_load_error(self, msg: str):
        self._loading_label.setText(f"获取文件列表失败：{msg}")

    def _select_all(self):
        self._list_widget.blockSignals(True)
        for i in range(self._list_widget.count()):
            self._list_widget.item(i).setCheckState(Qt.CheckState.Checked)
        self._list_widget.blockSignals(False)
        self._update_status()

    def _select_none(self):
        self._list_widget.blockSignals(True)
        for i in range(self._list_widget.count()):
            self._list_widget.item(i).setCheckState(Qt.CheckState.Unchecked)
        self._list_widget.blockSignals(False)
        self._update_status()

    def _on_item_changed(self, _item):
        self._update_status()

    def _update_status(self):
        count = 0
        total_strings = 0
        file_totals = {f["id"]: f.get("total", 0) for f in self._file_data}
        for i in range(self._list_widget.count()):
            item = self._list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                count += 1
                total_strings += file_totals.get(item.data(Qt.ItemDataRole.UserRole), 0)
        self._status_label.setText(f"已选 {count} 个文件，共 {total_strings} 条词条")
        self._ok_btn.setEnabled(count > 0)

    def selected_file_ids(self) -> list[int]:
        result = []
        for i in range(self._list_widget.count()):
            item = self._list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                result.append(item.data(Qt.ItemDataRole.UserRole))
        return result


class DownloadCard(OpCard):

    def __init__(self, ctx, run_worker, parent=None):
        super().__init__(
            "从 ParaTranz 下载合并",
            "从当前已选项目拉取译文，按 key 合并到本地集合（stage >= 1 的词条）。",
            "下载合并",
            parent,
        )
        self._ctx = ctx
        self._run_worker = run_worker
        self.btn.clicked.connect(self._do_download)

    def _do_download(self):
        collection = self._ctx.collection
        project = self._ctx.current_project
        if not collection or not project:
            return
        project_id = project.get("id")
        config = self._ctx.config

        dlg = _FileSelectDialog(config, project_id, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        file_ids = dlg.selected_file_ids()

        def _download_factory(progress_cb):
            downloader = ParaTranzDownloader(config)
            return downloader.download_to_collection(
                project_id, collection,
                file_ids=file_ids, progress_callback=progress_cb)

        def _on_done(result):
            self._ctx.collection_changed.emit(self._ctx.collection)
            QMessageBox.information(
                self, "下载合并完成",
                f"已合并：{result.merged} 条\n"
                f"未匹配：{result.skipped_no_match} 条\n"
                f"跳过（stage 不足）：{result.skipped_low_stage} 条",
            )

        self._run_worker(
            fn_factory=_download_factory,
            on_result=_on_done,
            on_error=lambda e: QMessageBox.critical(self, "下载失败", e),
            progress_total=0,
            progress_msg="正在从 ParaTranz 下载合并…",
        )
