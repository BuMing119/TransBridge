"""
FilesTab: 文件管理标签页，列表 + 文件详情 + 上传/更新/下载操作。
"""

from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QGroupBox,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QLabel, QProgressBar, QFileDialog, QMessageBox,
    QDialog, QFormLayout, QLineEdit, QCheckBox,
)
from PyQt6.QtCore import Qt

from src.transbridge.paratranz.api.paratranz_files_api import ParatranzFilesAPI
from ..workers import ApiWorker


def _fmt_pct(translated, total):
    if total:
        return f"{translated / total * 100:.0f}%"
    return "—"


class FilesTab(QWidget):

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._workers: list[ApiWorker] = []
        self._files: list[dict] = []
        self._project_id: int | None = None
        self._gen = 0
        self._init_ui()
        ctx.project_selected.connect(self._on_project_changed)

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 工具栏
        toolbar = QHBoxLayout()
        self._refresh_btn = QPushButton("刷新")
        self._refresh_btn.clicked.connect(self.load_files)
        self._upload_btn = QPushButton("上传新文件")
        self._upload_btn.clicked.connect(self._upload_new)
        self._reupload_btn = QPushButton("更新原文")
        self._reupload_btn.clicked.connect(self._reupload)
        self._import_btn = QPushButton("导入译文")
        self._import_btn.clicked.connect(self._import_translation)
        self._download_btn = QPushButton("下载翻译")
        self._download_btn.clicked.connect(self._download_translation)
        self._delete_btn = QPushButton("删除文件")
        self._delete_btn.setStyleSheet("color: red;")
        self._delete_btn.clicked.connect(self._delete_file)

        for btn in (self._refresh_btn, self._upload_btn, self._reupload_btn,
                    self._import_btn, self._download_btn, self._delete_btn):
            toolbar.addWidget(btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # 进度行
        progress_row = QHBoxLayout()
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)  # 不确定模式
        self._progress_bar.setFixedHeight(14)
        self._progress_bar.hide()
        self._progress_lbl = QLabel("")
        self._progress_lbl.hide()
        progress_row.addWidget(self._progress_bar)
        progress_row.addWidget(self._progress_lbl)
        layout.addLayout(progress_row)

        # 主分割区
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)

        # 上半：文件列表
        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels(
            ["文件名", "总词条", "已翻译", "有疑问", "已检查", "已审核", "进度", "最后修改"]
        )
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, 8):
            self._table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        splitter.addWidget(self._table)

        # 下半：文件详情
        detail_box = QGroupBox("文件详情")
        detail_layout = QFormLayout(detail_box)
        self._det_id = QLabel("—")
        self._det_hash = QLabel("—")
        self._det_words = QLabel("—")
        self._det_created = QLabel("—")
        self._det_updated = QLabel("—")
        for label, widget in (
            ("文件 ID", self._det_id),
            ("原文 MD5", self._det_hash),
            ("总词数", self._det_words),
            ("创建时间", self._det_created),
            ("更新时间", self._det_updated),
        ):
            detail_layout.addRow(label + ":", widget)
        splitter.addWidget(detail_box)
        splitter.setSizes([400, 160])

        layout.addWidget(splitter, stretch=1)

        self._update_buttons(False)

    def _update_buttons(self, has_selection: bool):
        is_admin = self._ctx.is_admin()
        self._upload_btn.setEnabled(bool(self._project_id) and is_admin)
        self._reupload_btn.setEnabled(has_selection and is_admin)
        self._import_btn.setEnabled(has_selection and is_admin)
        self._download_btn.setEnabled(has_selection)
        self._delete_btn.setEnabled(has_selection and is_admin)

    def _on_project_changed(self, project: dict | None):
        self._project_id = project.get("id") if project else None
        self._files = []
        self._table.setRowCount(0)
        self._update_buttons(False)
        if self._project_id:
            self.load_files()

    def load_files(self):
        if not self._project_id:
            return
        config = self._ctx.config
        pid = self._project_id
        self._gen += 1
        gen = self._gen

        def _fetch():
            api = ParatranzFilesAPI(token=config.token, config=config)
            data = api.list_files(pid)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                for k in ("data", "results"):
                    if isinstance(data.get(k), list):
                        return data[k]
            return []

        def _on_done(files):
            if self._gen != gen:
                return
            self._on_files_loaded(files)

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
        w.error.connect(lambda e: QMessageBox.warning(self, "加载失败", e))
        w.start()
        self._workers.append(w)

    def _on_files_loaded(self, files: list):
        self._files = files
        self._table.setRowCount(len(files))
        for row, f in enumerate(files):
            total = f.get("total", 0)
            translated = f.get("translated", 0)
            cells = [
                f.get("name", ""),
                str(total),
                str(translated),
                str(f.get("disputed", 0)),
                str(f.get("checked", 0)),
                str(f.get("reviewed", 0)),
                _fmt_pct(translated, total),
                f.get("modifiedAt", "")[:10] if f.get("modifiedAt") else "—",
            ]
            for col, val in enumerate(cells):
                item = QTableWidgetItem(val)
                item.setData(Qt.ItemDataRole.UserRole, f)
                self._table.setItem(row, col, item)

    def _selected_file(self) -> dict | None:
        rows = self._table.selectedItems()
        if not rows:
            return None
        return rows[0].data(Qt.ItemDataRole.UserRole)

    def _on_selection_changed(self):
        f = self._selected_file()
        self._update_buttons(f is not None)
        if f:
            self._det_id.setText(str(f.get("id", "—")))
            self._det_hash.setText(f.get("hash", "—") or "—")
            self._det_words.setText(str(f.get("words", "—")))
            self._det_created.setText(str(f.get("createdAt", "—"))[:19])
            self._det_updated.setText(str(f.get("updatedAt", "—"))[:19])

    def _show_progress(self, msg: str):
        self._progress_lbl.setText(msg)
        self._progress_bar.show()
        self._progress_lbl.show()
        self._set_op_buttons_enabled(False)

    def _hide_progress(self):
        self._progress_bar.hide()
        self._progress_lbl.hide()
        self._progress_lbl.setText("")
        f = self._selected_file()
        self._update_buttons(f is not None)

    def _set_op_buttons_enabled(self, enabled: bool):
        for btn in (self._upload_btn, self._reupload_btn,
                    self._import_btn, self._download_btn, self._delete_btn):
            btn.setEnabled(enabled)

    def _run_file_op(self, fn, *, progress_msg: str, success_msg: str, error_title: str,
                     on_success=None):
        self._show_progress(progress_msg)

        def _on_done(result):
            self._hide_progress()
            QMessageBox.information(self, "成功", success_msg)
            if on_success:
                on_success(result)

        def _on_error(e):
            self._hide_progress()
            QMessageBox.critical(self, error_title, e)

        w = ApiWorker(fn)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)

    def _upload_new(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择文件")
        if not path:
            return
        config = self._ctx.config
        pid = self._project_id

        def _upload():
            api = ParatranzFilesAPI(token=config.token, config=config)
            return api.upload_file(pid, path)

        self._run_file_op(_upload, progress_msg="正在上传文件…",
                          success_msg="文件已上传", error_title="上传失败",
                          on_success=lambda _: self.load_files())

    def _reupload(self):
        f = self._selected_file()
        if not f:
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择新原文文件")
        if not path:
            return
        config = self._ctx.config
        pid = self._project_id
        fid = f.get("id")

        def _reupload():
            api = ParatranzFilesAPI(token=config.token, config=config)
            return api.reupload_file(pid, fid, path)

        self._run_file_op(_reupload, progress_msg="正在更新原文…",
                          success_msg="原文已更新", error_title="更新失败",
                          on_success=lambda _: self.load_files())

    def _import_translation(self):
        f = self._selected_file()
        if not f:
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择译文文件")
        if not path:
            return
        config = self._ctx.config
        pid = self._project_id
        fid = f.get("id")

        def _import():
            api = ParatranzFilesAPI(token=config.token, config=config)
            return api.update_file_translation(pid, fid, path)

        self._run_file_op(_import, progress_msg="正在导入译文…",
                          success_msg="译文已导入", error_title="导入失败",
                          on_success=lambda _: self.load_files())

    def _download_translation(self):
        f = self._selected_file()
        if not f:
            return
        save_path, _ = QFileDialog.getSaveFileName(
            self, "保存译文文件", f.get("name", "translation") + ".json",
            "JSON 文件 (*.json)"
        )
        if not save_path:
            return
        config = self._ctx.config
        pid = self._project_id
        fid = f.get("id")

        def _download():
            import json
            api = ParatranzFilesAPI(token=config.token, config=config)
            data = api.get_file_translation(pid, fid)
            Path(save_path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            return save_path

        self._show_progress("正在下载翻译数据…")

        def _on_done(p):
            self._hide_progress()
            QMessageBox.information(self, "成功", f"已保存至：{p}")

        def _on_error(e):
            self._hide_progress()
            QMessageBox.critical(self, "下载失败", e)

        w = ApiWorker(_download)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)

    def _delete_file(self):
        f = self._selected_file()
        if not f:
            return
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除文件「{f.get('name', '')}」吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        config = self._ctx.config
        pid = self._project_id
        fid = f.get("id")

        def _delete():
            api = ParatranzFilesAPI(token=config.token, config=config)
            return api.delete_file(pid, fid)

        w = ApiWorker(_delete)
        w.result.connect(lambda _: (QMessageBox.information(self, "成功", "文件已删除"), self.load_files()))
        w.error.connect(lambda e: QMessageBox.critical(self, "删除失败", e))
        w.start()
        self._workers.append(w)
