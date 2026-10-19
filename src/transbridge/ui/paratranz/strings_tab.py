"""
StringsTab: 词条管理标签页，支持筛选、浏览及批量操作。
双击词条后弹出 StringDetailDialog 进行查看和编辑。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from transbridge.paratranz.api.paratranz_files_api import ParatranzFilesAPI
from transbridge.paratranz.api.paratranz_strings_api import ParatranzStringsAPI
from transbridge.ui.foundation.adapters import DomainBrushes, ThemeView
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, SemanticState

from ..workers import ApiWorker
from ._delete_confirmation import confirm_cloud_removal
from ._layout_stability import configure_stable_table_columns
from ._strings_common import _STAGE_LABELS, _extract_list
from .string_detail_dialog import StringDetailDialog
from .string_dialogs import _BatchStageDialog, _CreateStringDialog


class StringsTab(QWidget):
    def __init__(
        self,
        ctx,
        parent=None,
        *,
        theme_view: ThemeView | None = None,
        domain_brushes: DomainBrushes | None = None,
    ):
        super().__init__(parent)
        self._ctx = ctx
        self._theme_view = theme_view
        self._domain_brushes = domain_brushes
        self._workers: list[ApiWorker] = []
        self._strings: list[dict] = []
        self._files: list[dict] = []
        self._project_id: int | None = None
        self._files_gen = 0
        self._strings_gen = 0
        self._loaded_query: dict | None = None
        self._init_ui()
        ctx.project_selected.connect(self._on_project_changed)
        ctx.paratranz_permissions_changed.connect(self._refresh_permissions)

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 筛选栏
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("文件:"))
        self._file_combo = QComboBox()
        self._file_combo.setMinimumWidth(140)
        self._file_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._file_combo.setMinimumContentsLength(8)
        filter_row.addWidget(self._file_combo, 1)

        filter_row.addWidget(QLabel("状态:"))
        self._stage_combo = QComboBox()
        for val, name in _STAGE_LABELS.items():
            self._stage_combo.addItem(name, val)
        filter_row.addWidget(self._stage_combo)
        layout.addLayout(filter_row)

        page_row = QHBoxLayout()
        page_row.addWidget(QLabel("页码:"))
        self._page_spin = QSpinBox()
        self._page_spin.setRange(1, 9999)
        self._page_spin.setMinimumWidth(60)
        page_row.addWidget(self._page_spin)

        page_row.addWidget(QLabel("每页:"))
        self._page_size_combo = QComboBox()
        for sz in (50, 100, 200, 500):
            self._page_size_combo.addItem(str(sz), sz)
        page_row.addWidget(self._page_size_combo)

        self._query_btn = QPushButton("查询")
        self._query_btn.setEnabled(False)
        self._query_btn.clicked.connect(self.load_strings)
        page_row.addStretch()
        page_row.addWidget(self._query_btn)
        layout.addLayout(page_row)

        # 工具栏
        toolbar = QHBoxLayout()
        self._create_btn = QPushButton("创建词条")
        self._create_btn.clicked.connect(self._create_string)
        self._create_btn.setEnabled(False)
        self._batch_stage_btn = QPushButton("批量设置状态")
        self._batch_stage_btn.clicked.connect(self._batch_set_stage)
        self._batch_del_btn = QPushButton("批量删除")
        self._batch_del_btn.setAccessibleName("批量删除所选词条")
        ComponentStyle.apply_static(self._batch_del_btn, ComponentKind.BUTTON)
        ComponentStyle.apply_state(self._batch_del_btn, SemanticState.ERROR)
        self._batch_del_btn.clicked.connect(self._batch_delete)
        hint = QLabel("双击词条可查看详情/编辑")
        hint.setWordWrap(True)
        hint.setAccessibleName("词条操作提示")
        ComponentStyle.apply_state(hint, SemanticState.INFO)
        toolbar.addWidget(self._create_btn)
        for btn in (self._batch_stage_btn, self._batch_del_btn):
            btn.setEnabled(False)
            toolbar.addWidget(btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        layout.addWidget(hint)
        self._query_status = QLabel("选择项目查看词条")
        self._query_status.setTextFormat(Qt.TextFormat.PlainText)
        self._query_status.setWordWrap(True)
        self._query_status.setAccessibleName("词条查询状态")
        layout.addWidget(self._query_status)

        # 词条列表
        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(["键名", "原文", "译文", "状态", "所属文件", "操作者", "更新时间"])
        configure_stable_table_columns(
            self._table,
            fixed_widths={0: 220, 3: 90, 4: 160, 5: 120, 6: 110},
            stretch_columns=(1, 2),
        )
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self._table.setAccessibleName("ParaTranz 词条列表")
        self._table.setAccessibleDescription("词条状态同时显示为文字；双击所选行可查看或编辑详情。")
        ComponentStyle.apply_static(self._table, ComponentKind.TABLE)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.itemDoubleClicked.connect(self._on_double_clicked)
        layout.addWidget(self._table, stretch=1)

    def _on_project_changed(self, project):
        self._files_gen += 1
        self._strings_gen += 1
        self._project_id = project.get("id") if project else None
        self._strings = []
        self._files = []
        self._loaded_query = None
        self._table.setRowCount(0)
        self._file_combo.clear()
        self._file_combo.addItem("全部文件", None)
        self._page_spin.setValue(1)
        self._query_btn.setEnabled(bool(self._project_id))
        self._query_status.setText("选择项目查看词条")
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

        def _on_error(error):
            if self._files_gen != gen:
                return
            answer = QMessageBox.warning(
                self,
                "无法加载文件列表",
                f"文件列表加载失败：{error}\n\n点击“重试”重新加载文件筛选；取消后仍可查询全部文件的词条。",
                QMessageBox.StandardButton.Retry | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Retry and self._files_gen == gen and self._project_id == pid:
                self._load_files_for_filter()

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)

    def _query_options(self) -> dict:
        stage = self._stage_combo.currentData()
        return {
            "file_id": self._file_combo.currentData(),
            "stage": stage if stage != -2 else None,
            "page": self._page_spin.value(),
            "page_size": self._page_size_combo.currentData(),
        }

    def load_strings(self):
        if not self._project_id:
            return
        config = self._ctx.config
        pid = self._project_id
        query = self._query_options()
        self._strings_gen += 1
        gen = self._strings_gen
        self._strings = []
        self._loaded_query = None
        self._table.setRowCount(0)
        self._query_status.setText(f"正在加载第 {query['page']} 页…")

        def _fetch():
            api = ParatranzStringsAPI(token=config.token, config=config)
            return _extract_list(
                api.list_strings(
                    pid, page=query["page"], page_size=query["page_size"], file=query["file_id"], stage=query["stage"]
                )
            )

        def _on_done(strings):
            if self._strings_gen != gen:
                return
            self._loaded_query = query
            self._on_strings_loaded(strings)

        def _on_error(error):
            if self._strings_gen == gen:
                self._query_status.setText(f"第 {query['page']} 页加载失败：{error}\n请点击查询重试。")

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)

    def _on_strings_loaded(self, strings: list):
        self._strings = strings
        if self._loaded_query is None:
            self._loaded_query = self._query_options()
        page = self._loaded_query["page"]
        self._query_status.setText(f"第 {page} 页 · 共 {len(strings)} 条" if strings else f"第 {page} 页没有匹配的词条")
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
                self._table.setItem(row, col, item)
            self._apply_stage_brush(row, stage)

    def apply_domain_brushes(self, domain_brushes: DomainBrushes) -> None:
        """Refresh only visual roles; row items, selection and loaded strings stay intact."""

        self._domain_brushes = domain_brushes
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 3)
            payload = None if item is None else item.data(Qt.ItemDataRole.UserRole)
            stage = payload.get("stage", 0) if isinstance(payload, dict) else 0
            self._apply_stage_brush(row, stage)
        self._table.viewport().update()

    def _apply_stage_brush(self, row: int, stage: int) -> None:
        item = self._table.item(row, 3)
        if item is None:
            return
        item.setToolTip(f"状态：{_STAGE_LABELS.get(stage, str(stage))}")
        if self._domain_brushes is None:
            return
        brush = self._domain_brushes.stage(stage)
        item.setForeground(brush.foreground)
        item.setBackground(brush.background)

    def _on_selection_changed(self):
        rows = {i.row() for i in self._table.selectedItems()}
        is_admin = self._ctx.is_admin()
        self._batch_stage_btn.setEnabled(len(rows) >= 1 and is_admin)
        self._batch_del_btn.setEnabled(len(rows) >= 1 and is_admin)

    def _refresh_permissions(self):
        self._create_btn.setEnabled(bool(self._project_id) and self._ctx.is_admin())
        self._on_selection_changed()

    def _on_double_clicked(self, item: QTableWidgetItem):
        row = item.row()
        if not self._strings or row >= len(self._strings):
            return
        query = self._loaded_query or self._query_options()
        dlg = StringDetailDialog(
            self._strings,
            row,
            self._ctx,
            self._project_id,
            **query,
            parent=self,
            theme_view=self._theme_view,
            domain_brushes=self._domain_brushes,
        )
        dlg.strings_updated.connect(self.load_strings)
        dlg.exec()
        if dlg.was_modified():
            self.load_strings()

    def _batch_set_stage(self):
        pid = self._project_id
        rows = list({i.row() for i in self._table.selectedItems()})
        ids = [self._table.item(r, 0).data(Qt.ItemDataRole.UserRole).get("id") for r in rows]

        dlg = _BatchStageDialog(len(ids), self)
        if not dlg.exec():
            return
        if pid != self._project_id:
            self._query_status.setText("项目已切换，未提交此前的词条修改。请重新选择词条。")
            return
        target_stage = dlg.get_stage()
        config = self._ctx.config

        def _update():
            api = ParatranzStringsAPI(token=config.token, config=config)
            return api.batch_strings(pid, "update", ids, stage=target_stage)

        w = ApiWorker(_update)
        w.result.connect(lambda _: self.load_strings() if self._project_id == pid else None)
        w.error.connect(lambda e: QMessageBox.critical(self, "操作失败", e) if self._project_id == pid else None)
        w.start()
        self._workers.append(w)

    def _batch_delete(self):
        pid = self._project_id
        rows = list({i.row() for i in self._table.selectedItems()})
        ids = [self._table.item(r, 0).data(Qt.ItemDataRole.UserRole).get("id") for r in rows]
        reply = confirm_cloud_removal(
            self,
            f"确定要删除选中的 {len(ids)} 条词条吗？",
            action_text="删除词条",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if pid != self._project_id:
            self._query_status.setText("项目已切换，未删除此前选择的词条。请重新选择词条。")
            return
        config = self._ctx.config

        def _delete():
            api = ParatranzStringsAPI(token=config.token, config=config)
            return api.batch_strings(pid, "delete", ids)

        w = ApiWorker(_delete)
        w.result.connect(lambda _: self.load_strings() if self._project_id == pid else None)
        w.error.connect(lambda e: QMessageBox.critical(self, "删除失败", e) if self._project_id == pid else None)
        w.start()
        self._workers.append(w)

    def _create_string(self):
        pid = self._project_id
        dlg = _CreateStringDialog(self._files, self)
        if not dlg.exec():
            return
        if pid != self._project_id:
            self._query_status.setText("项目已切换，未在当前项目创建词条。请重新填写。")
            return
        data = dlg.get_data()
        config = self._ctx.config

        def _create():
            api = ParatranzStringsAPI(token=config.token, config=config)
            return api.create_string(pid, data)

        w = ApiWorker(_create)
        w.result.connect(lambda _: self.load_strings() if self._project_id == pid else None)
        w.error.connect(lambda e: QMessageBox.critical(self, "创建失败", e) if self._project_id == pid else None)
        w.start()
        self._workers.append(w)
