"""
StringsTab: 词条管理标签页，支持筛选、浏览及批量操作。
双击词条后弹出 StringDetailDialog 进行查看和编辑。
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QLabel, QComboBox, QSpinBox,
    QMessageBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from transbridge.paratranz.api.paratranz_strings_api import ParatranzStringsAPI
from transbridge.paratranz.api.paratranz_files_api import ParatranzFilesAPI
from ..workers import ApiWorker
from ._strings_common import _STAGE_LABELS, _STAGE_COLORS, _extract_list
from .string_detail_dialog import StringDetailDialog
from .string_dialogs import _CreateStringDialog, _BatchStageDialog


class StringsTab(QWidget):

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._workers: list[ApiWorker] = []
        self._strings: list[dict] = []
        self._files: list[dict] = []
        self._project_id: int | None = None
        self._files_gen = 0
        self._strings_gen = 0
        self._init_ui()
        ctx.project_selected.connect(self._on_project_changed)

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 筛选栏
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("文件:"))
        self._file_combo = QComboBox()
        self._file_combo.setMinimumWidth(140)
        filter_row.addWidget(self._file_combo)

        filter_row.addWidget(QLabel("状态:"))
        self._stage_combo = QComboBox()
        for val, name in _STAGE_LABELS.items():
            self._stage_combo.addItem(name, val)
        filter_row.addWidget(self._stage_combo)

        filter_row.addWidget(QLabel("页码:"))
        self._page_spin = QSpinBox()
        self._page_spin.setRange(1, 9999)
        self._page_spin.setFixedWidth(60)
        filter_row.addWidget(self._page_spin)

        filter_row.addWidget(QLabel("每页:"))
        self._page_size_combo = QComboBox()
        for sz in (50, 100, 200, 500):
            self._page_size_combo.addItem(str(sz), sz)
        filter_row.addWidget(self._page_size_combo)

        refresh_btn = QPushButton("查询")
        refresh_btn.clicked.connect(self.load_strings)
        filter_row.addWidget(refresh_btn)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        # 工具栏
        toolbar = QHBoxLayout()
        self._create_btn = QPushButton("创建词条")
        self._create_btn.clicked.connect(self._create_string)
        self._create_btn.setEnabled(False)
        self._batch_stage_btn = QPushButton("批量设置状态")
        self._batch_stage_btn.clicked.connect(self._batch_set_stage)
        self._batch_del_btn = QPushButton("批量删除")
        self._batch_del_btn.setStyleSheet("color: red;")
        self._batch_del_btn.clicked.connect(self._batch_delete)
        hint = QLabel("双击词条可查看详情/编辑")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        toolbar.addWidget(self._create_btn)
        for btn in (self._batch_stage_btn, self._batch_del_btn):
            btn.setEnabled(False)
            toolbar.addWidget(btn)
        toolbar.addStretch()
        toolbar.addWidget(hint)
        layout.addLayout(toolbar)

        # 词条列表
        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(
            ["键名", "原文", "译文", "状态", "所属文件", "操作者", "更新时间"]
        )
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for i in (3, 4, 5, 6):
            self._table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.itemDoubleClicked.connect(self._on_double_clicked)
        layout.addWidget(self._table, stretch=1)

    def _on_project_changed(self, project):
        self._project_id = project.get("id") if project else None
        self._strings = []
        self._table.setRowCount(0)
        self._file_combo.clear()
        self._file_combo.addItem("全部文件", None)
        self._create_btn.setEnabled(bool(self._project_id) and self._ctx.is_admin())
        if self._project_id:
            self._load_files_for_filter()
            self.load_strings()

    def _load_files_for_filter(self):
        config = self._ctx.config
        pid = self._project_id
        self._files_gen += 1
        gen = self._files_gen

        def _fetch():
            api = ParatranzFilesAPI(token=config.token, config=config)
            data = api.list_files(pid)
            return data if isinstance(data, list) else []

        def _on_done(files):
            if self._files_gen != gen:
                return
            self._files = files
            self._file_combo.clear()
            self._file_combo.addItem("全部文件", None)
            for f in files:
                self._file_combo.addItem(f.get("name", str(f.get("id"))), f.get("id"))

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
        w.error.connect(lambda _: None)
        w.start()
        self._workers.append(w)

    def load_strings(self):
        if not self._project_id:
            return
        config = self._ctx.config
        pid = self._project_id
        file_id = self._file_combo.currentData()
        stage_val = self._stage_combo.currentData()
        stage = stage_val if stage_val != -2 else None
        page = self._page_spin.value()
        page_size = self._page_size_combo.currentData()
        self._strings_gen += 1
        gen = self._strings_gen

        def _fetch():
            api = ParatranzStringsAPI(token=config.token, config=config)
            return _extract_list(api.list_strings(pid, page=page, page_size=page_size,
                                                   file=file_id, stage=stage))

        def _on_done(strings):
            if self._strings_gen != gen:
                return
            self._on_strings_loaded(strings)

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
        w.error.connect(lambda e: QMessageBox.warning(self, "加载失败", e))
        w.start()
        self._workers.append(w)

    def _on_strings_loaded(self, strings: list):
        self._strings = strings
        self._table.setRowCount(len(strings))
        for row, s in enumerate(strings):
            stage = s.get("stage", 0)
            stage_text = _STAGE_LABELS.get(stage, str(stage))
            file_info = s.get("file") or {}
            user_info = s.get("user") or {}
            cells = [
                s.get("key", ""),
                s.get("original", "")[:80],
                s.get("translation", "")[:80],
                stage_text,
                file_info.get("name", "—") if isinstance(file_info, dict) else "—",
                user_info.get("nickname", "—") if isinstance(user_info, dict) else "—",
                str(s.get("updatedAt", ""))[:10],
            ]
            for col, val in enumerate(cells):
                item = QTableWidgetItem(val)
                item.setData(Qt.ItemDataRole.UserRole, s)
                if col == 3:
                    item.setForeground(QColor(_STAGE_COLORS.get(stage, "#000000")))
                self._table.setItem(row, col, item)

    def _on_selection_changed(self):
        rows = {i.row() for i in self._table.selectedItems()}
        is_admin = self._ctx.is_admin()
        self._batch_stage_btn.setEnabled(len(rows) >= 1 and is_admin)
        self._batch_del_btn.setEnabled(len(rows) >= 1 and is_admin)

    def _on_double_clicked(self, item: QTableWidgetItem):
        row = item.row()
        if not self._strings or row >= len(self._strings):
            return
        file_id = self._file_combo.currentData()
        stage_val = self._stage_combo.currentData()
        stage = stage_val if stage_val != -2 else None
        page = self._page_spin.value()
        page_size = self._page_size_combo.currentData()
        dlg = StringDetailDialog(
            self._strings, row, self._ctx, self._project_id,
            file_id=file_id, stage=stage, page=page, page_size=page_size,
            parent=self,
        )
        dlg.strings_updated.connect(self.load_strings)
        dlg.exec()
        if dlg.was_modified():
            self.load_strings()

    def _batch_set_stage(self):
        rows = list({i.row() for i in self._table.selectedItems()})
        ids = [self._table.item(r, 0).data(Qt.ItemDataRole.UserRole).get("id") for r in rows]

        dlg = _BatchStageDialog(len(ids), self)
        if not dlg.exec():
            return
        target_stage = dlg.get_stage()
        config = self._ctx.config
        pid = self._project_id

        def _update():
            api = ParatranzStringsAPI(token=config.token, config=config)
            return api.batch_strings(pid, "update", ids, stage=target_stage)

        w = ApiWorker(_update)
        w.result.connect(lambda _: self.load_strings())
        w.error.connect(lambda e: QMessageBox.critical(self, "操作失败", e))
        w.start()
        self._workers.append(w)

    def _batch_delete(self):
        rows = list({i.row() for i in self._table.selectedItems()})
        ids = [self._table.item(r, 0).data(Qt.ItemDataRole.UserRole).get("id") for r in rows]
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除选中的 {len(ids)} 条词条吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        config = self._ctx.config
        pid = self._project_id

        def _delete():
            api = ParatranzStringsAPI(token=config.token, config=config)
            return api.batch_strings(pid, "delete", ids)

        w = ApiWorker(_delete)
        w.result.connect(lambda _: self.load_strings())
        w.error.connect(lambda e: QMessageBox.critical(self, "删除失败", e))
        w.start()
        self._workers.append(w)

    def _create_string(self):
        dlg = _CreateStringDialog(self._files, self)
        if not dlg.exec():
            return
        data = dlg.get_data()
        config = self._ctx.config
        pid = self._project_id

        def _create():
            api = ParatranzStringsAPI(token=config.token, config=config)
            return api.create_string(pid, data)

        w = ApiWorker(_create)
        w.result.connect(lambda _: self.load_strings())
        w.error.connect(lambda e: QMessageBox.critical(self, "创建失败", e))
        w.start()
        self._workers.append(w)