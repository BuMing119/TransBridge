"""
HistoryTab: 历史记录标签页，含词条变更历史和文件上传历史两个子标签。
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QLabel, QComboBox, QSpinBox, QLineEdit, QMessageBox,
    QDialog, QSplitter, QTextEdit,
)
from PyQt6.QtCore import Qt

from src.transbridge.paratranz.api.paratranz_history_api import ParatranzHistoryAPI
from ..workers import ApiWorker


def _extract_list(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("data", "results", "items"):
            if isinstance(data.get(k), list):
                return data[k]
    return []


class HistoryTab(QWidget):

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._workers: list[ApiWorker] = []
        self._project_id: int | None = None
        self._hist_gen = 0
        self._rev_gen = 0
        self._init_ui()
        ctx.project_selected.connect(self._on_project_changed)

    def _init_ui(self):
        layout = QVBoxLayout(self)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_history_tab(), "词条变更历史")
        self._tabs.addTab(self._build_revision_tab(), "文件上传历史")
        layout.addWidget(self._tabs)

    def _build_history_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        # 筛选栏
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("类型:"))
        self._type_combo = QComboBox()
        for val, name in (("", "全部"), ("text", "词条"), ("term", "术语"),
                          ("import", "导入"), ("comment", "评论")):
            self._type_combo.addItem(name, val)
        filter_row.addWidget(self._type_combo)

        filter_row.addWidget(QLabel("用户ID:"))
        self._uid_input = QLineEdit()
        self._uid_input.setPlaceholderText("可选")
        self._uid_input.setFixedWidth(80)
        filter_row.addWidget(self._uid_input)

        filter_row.addWidget(QLabel("词条ID:"))
        self._tid_input = QLineEdit()
        self._tid_input.setPlaceholderText("可选")
        self._tid_input.setFixedWidth(80)
        filter_row.addWidget(self._tid_input)

        filter_row.addWidget(QLabel("页码:"))
        self._page_spin = QSpinBox()
        self._page_spin.setRange(1, 9999)
        self._page_spin.setFixedWidth(60)
        filter_row.addWidget(self._page_spin)

        self._hist_refresh_btn = QPushButton("查询")
        self._hist_refresh_btn.clicked.connect(self.load_history)
        filter_row.addWidget(self._hist_refresh_btn)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        self._hist_table = QTableWidget(0, 7)
        self._hist_table.setHorizontalHeaderLabels(
            ["时间", "操作者", "操作类型", "词条键名", "修改字段", "修改前", "修改后"]
        )
        self._hist_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for i in (1, 2, 3, 4):
            self._hist_table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self._hist_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self._hist_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self._hist_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._hist_table.itemDoubleClicked.connect(self._on_hist_row_double_clicked)
        layout.addWidget(self._hist_table, stretch=1)
        return w

    def _build_revision_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        rev_toolbar = QHBoxLayout()
        self._rev_refresh_btn = QPushButton("刷新")
        self._rev_refresh_btn.clicked.connect(self.load_revisions)
        rev_toolbar.addWidget(self._rev_refresh_btn)
        rev_toolbar.addStretch()
        layout.addLayout(rev_toolbar)

        self._rev_table = QTableWidget(0, 7)
        self._rev_table.setHorizontalHeaderLabels(
            ["时间", "文件", "操作类型", "操作者 ID", "新增", "更新", "删除"]
        )
        self._rev_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._rev_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for i in (2, 3, 4, 5, 6):
            self._rev_table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self._rev_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._rev_table, stretch=1)
        return w

    def _on_project_changed(self, project):
        self._project_id = project.get("id") if project else None
        self._hist_table.setRowCount(0)
        self._rev_table.setRowCount(0)
        if self._project_id:
            self.load_history()
            self.load_revisions()

    def load_history(self):
        if not self._project_id:
            return
        config = self._ctx.config
        pid = self._project_id
        history_type = self._type_combo.currentData() or None
        uid_text = self._uid_input.text().strip()
        uid = int(uid_text) if uid_text.isdigit() else None
        tid_text = self._tid_input.text().strip()
        tid = int(tid_text) if tid_text.isdigit() else None
        page = self._page_spin.value()
        self._hist_gen += 1
        gen = self._hist_gen

        def _fetch():
            api = ParatranzHistoryAPI(token=config.token, config=config)
            return _extract_list(api.get_project_history(pid, page=page, uid=uid,
                                                          tid=tid, history_type=history_type))

        def _on_done(records):
            if self._hist_gen != gen:
                return
            self._on_history_loaded(records)

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
        w.error.connect(lambda e: QMessageBox.warning(self, "加载失败", e))
        w.start()
        self._workers.append(w)

    def _on_history_loaded(self, records: list):
        self._hist_table.setRowCount(len(records))
        for row, r in enumerate(records):
            user = r.get("user") or {}
            target = r.get("target") or {}
            from_full = str(r.get("from", ""))
            to_full = str(r.get("to", ""))
            cells = [
                str(r.get("createdAt", ""))[:19],
                user.get("nickname", str(r.get("uid", "—"))) if isinstance(user, dict) else "—",
                str(r.get("operation", "—")),
                target.get("key", "—") if isinstance(target, dict) else "—",
                str(r.get("field", "—")),
                from_full[:60],
                to_full[:60],
            ]
            for col, val in enumerate(cells):
                cell_item = QTableWidgetItem(val)
                # 第5、6列存储完整内容供双击详情使用
                if col == 5:
                    cell_item.setData(Qt.ItemDataRole.UserRole, from_full)
                elif col == 6:
                    cell_item.setData(Qt.ItemDataRole.UserRole, to_full)
                self._hist_table.setItem(row, col, cell_item)

    def load_revisions(self):
        if not self._project_id:
            return
        config = self._ctx.config
        pid = self._project_id
        self._rev_gen += 1
        gen = self._rev_gen

        def _fetch():
            api = ParatranzHistoryAPI(token=config.token, config=config)
            return _extract_list(api.list_file_revisions(pid))

        def _on_done(records):
            if self._rev_gen != gen:
                return
            self._on_revisions_loaded(records)

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
        w.error.connect(lambda _: None)
        w.start()
        self._workers.append(w)

    def _on_revisions_loaded(self, records: list):
        self._rev_table.setRowCount(len(records))
        for row, r in enumerate(records):
            cells = [
                str(r.get("createdAt", ""))[:19],
                r.get("name", "—"),
                str(r.get("type", "—")),
                str(r.get("uid", "—")),
                str(r.get("insert", 0)),
                str(r.get("update", 0)),
                str(r.get("remove", 0)),
            ]
            for col, val in enumerate(cells):
                self._rev_table.setItem(row, col, QTableWidgetItem(val))

    def _on_hist_row_double_clicked(self, item: QTableWidgetItem):
        row = item.row()
        from_text = self._hist_table.item(row, 5)
        to_text = self._hist_table.item(row, 6)
        from_val = from_text.data(Qt.ItemDataRole.UserRole) if from_text else ""
        to_val = to_text.data(Qt.ItemDataRole.UserRole) if to_text else ""

        dlg = QDialog(self)
        dlg.setWindowTitle("变更详情")
        dlg.resize(700, 400)
        layout = QVBoxLayout(dlg)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_box = QWidget()
        left_layout = QVBoxLayout(left_box)
        left_layout.addWidget(QLabel("修改前："))
        left_edit = QTextEdit()
        left_edit.setReadOnly(True)
        left_edit.setPlainText(from_val or "（空）")
        left_layout.addWidget(left_edit)
        splitter.addWidget(left_box)

        right_box = QWidget()
        right_layout = QVBoxLayout(right_box)
        right_layout.addWidget(QLabel("修改后："))
        right_edit = QTextEdit()
        right_edit.setReadOnly(True)
        right_edit.setPlainText(to_val or "（空）")
        right_layout.addWidget(right_edit)
        splitter.addWidget(right_box)

        layout.addWidget(splitter)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)
        dlg.exec()
