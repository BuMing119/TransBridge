"""
TermsTab: 术语管理标签页。
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QGroupBox,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QLabel, QFormLayout, QLineEdit, QTextEdit,
    QComboBox, QCheckBox, QMessageBox, QDialog,
)
from PyQt6.QtCore import Qt

from src.transbridge.paratranz.api.paratranz_terms_api import ParatranzTermsAPI
from ..workers import ApiWorker


def _extract_list(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("data", "results", "items"):
            if isinstance(data.get(k), list):
                return data[k]
    return []


class TermFormDialog(QDialog):

    def __init__(self, term: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加术语" if term is None else "编辑术语")
        self.setMinimumWidth(360)
        self._init_ui(term or {})

    def _init_ui(self, term: dict):
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._term_input = QLineEdit(term.get("term", ""))
        form.addRow("原文 *", self._term_input)

        self._trans_input = QLineEdit(term.get("translation", ""))
        form.addRow("译文 *", self._trans_input)

        self._pos_combo = QComboBox()
        self._pos_combo.addItems(["", "noun", "verb", "adj", "adv"])
        pos = term.get("pos", "")
        idx = self._pos_combo.findText(pos)
        if idx >= 0:
            self._pos_combo.setCurrentIndex(idx)
        form.addRow("词性", self._pos_combo)

        self._note_input = QTextEdit(term.get("note", ""))
        self._note_input.setMaximumHeight(70)
        form.addRow("注释", self._note_input)

        self._case_check = QCheckBox("大小写敏感")
        self._case_check.setChecked(bool(term.get("caseSensitive", False)))
        form.addRow("", self._case_check)

        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("保存")
        ok_btn.clicked.connect(self._validate)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _validate(self):
        if not self._term_input.text().strip() or not self._trans_input.text().strip():
            QMessageBox.warning(self, "提示", "原文和译文不能为空")
            return
        self.accept()

    def get_data(self) -> dict:
        return {
            "term": self._term_input.text().strip(),
            "translation": self._trans_input.text().strip(),
            "pos": self._pos_combo.currentText() or None,
            "note": self._note_input.toPlainText().strip() or None,
            "caseSensitive": self._case_check.isChecked(),
        }


class TermsTab(QWidget):

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._workers: list[ApiWorker] = []
        self._terms: list[dict] = []
        self._project_id: int | None = None
        self._gen = 0
        self._init_ui()
        ctx.project_selected.connect(self._on_project_changed)

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 工具栏
        toolbar = QHBoxLayout()
        self._refresh_btn = QPushButton("刷新")
        self._refresh_btn.clicked.connect(self.load_terms)
        self._add_btn = QPushButton("添加术语")
        self._add_btn.clicked.connect(self._add_term)
        self._edit_btn = QPushButton("编辑")
        self._edit_btn.clicked.connect(self._edit_term)
        self._edit_btn.setEnabled(False)
        self._del_btn = QPushButton("删除")
        self._del_btn.setStyleSheet("color: red;")
        self._del_btn.clicked.connect(self._delete_term)
        self._del_btn.setEnabled(False)
        for btn in (self._refresh_btn, self._add_btn, self._edit_btn, self._del_btn):
            toolbar.addWidget(btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # 术语表
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(["原文", "译文", "词性", "注释", "变体", "大小写敏感"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        for i in (2, 3, 4, 5):
            self._table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self._table, stretch=1)

    def _on_project_changed(self, project):
        self._project_id = project.get("id") if project else None
        self._table.setRowCount(0)
        self._terms = []
        if self._project_id:
            self.load_terms()

    def load_terms(self):
        if not self._project_id:
            return
        config = self._ctx.config
        pid = self._project_id
        self._gen += 1
        gen = self._gen

        def _fetch():
            api = ParatranzTermsAPI(token=config.token, config=config)
            return _extract_list(api.list_terms(pid))

        def _on_done(terms):
            if self._gen != gen:
                return
            self._on_loaded(terms)

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
        w.error.connect(lambda e: QMessageBox.warning(self, "加载失败", e))
        w.start()
        self._workers.append(w)

    def _on_loaded(self, terms: list):
        self._terms = terms
        self._table.setRowCount(len(terms))
        for row, t in enumerate(terms):
            variants = ", ".join(t.get("variants") or [])
            cells = [
                t.get("term", ""),
                t.get("translation", ""),
                t.get("pos") or "—",
                (t.get("note") or "")[:60],
                variants or "—",
                "是" if t.get("caseSensitive") else "否",
            ]
            for col, val in enumerate(cells):
                item = QTableWidgetItem(val)
                item.setData(Qt.ItemDataRole.UserRole, t)
                self._table.setItem(row, col, item)

    def _selected_term(self) -> dict | None:
        rows = self._table.selectedItems()
        return rows[0].data(Qt.ItemDataRole.UserRole) if rows else None

    def _on_selection_changed(self):
        has = self._selected_term() is not None
        self._edit_btn.setEnabled(has)
        self._del_btn.setEnabled(has)

    def _add_term(self):
        dlg = TermFormDialog(parent=self)
        if not dlg.exec():
            return
        data = dlg.get_data()
        config = self._ctx.config
        pid = self._project_id

        def _create():
            api = ParatranzTermsAPI(token=config.token, config=config)
            return api.create_term(pid, data)

        w = ApiWorker(_create)
        w.result.connect(lambda _: self.load_terms())
        w.error.connect(lambda e: QMessageBox.critical(self, "添加失败", e))
        w.start()
        self._workers.append(w)

    def _edit_term(self):
        t = self._selected_term()
        if not t:
            return
        dlg = TermFormDialog(t, parent=self)
        if not dlg.exec():
            return
        data = dlg.get_data()
        config = self._ctx.config
        pid = self._project_id
        tid = t.get("id")

        def _update():
            api = ParatranzTermsAPI(token=config.token, config=config)
            return api.update_term(pid, tid, data)

        w = ApiWorker(_update)
        w.result.connect(lambda _: self.load_terms())
        w.error.connect(lambda e: QMessageBox.critical(self, "编辑失败", e))
        w.start()
        self._workers.append(w)

    def _delete_term(self):
        t = self._selected_term()
        if not t:
            return
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除术语「{t.get('term', '')}」吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        config = self._ctx.config
        pid = self._project_id
        tid = t.get("id")

        def _delete():
            api = ParatranzTermsAPI(token=config.token, config=config)
            return api.delete_term(pid, tid)

        w = ApiWorker(_delete)
        w.result.connect(lambda _: self.load_terms())
        w.error.connect(lambda e: QMessageBox.critical(self, "删除失败", e))
        w.start()
        self._workers.append(w)
