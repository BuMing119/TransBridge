"""
TermsTab: 术语管理标签页。
"""

import math

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QGroupBox,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QLabel, QFormLayout, QLineEdit, QTextEdit,
    QComboBox, QCheckBox, QMessageBox, QDialog, QDialogButtonBox,
)
from PyQt6.QtCore import Qt, QSize

from transbridge.paratranz.api.paratranz_terms_api import ParatranzTermsAPI
from transbridge.paratranz.api.paratranz_history_api import ParatranzHistoryAPI
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


class TermHistoryDialog(QDialog):
    """展示单条术语修改历史的对话框。"""

    def __init__(self, term_name: str, history: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"术语历史 — {term_name}")
        self.resize(QSize(760, 420))
        self._init_ui(history)

    def _init_ui(self, history: list):
        layout = QVBoxLayout(self)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(["时间", "操作者", "操作类型", "修改字段", "修改前", "修改后"])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setWordWrap(False)
        layout.addWidget(self._table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._fill(history)

    def _fill(self, history: list):
        self._table.setRowCount(len(history))
        for row, entry in enumerate(history):
            user = entry.get("user") or {}
            nickname = user.get("nickname") or str(entry.get("uid", ""))
            created_at = (entry.get("createdAt") or "")[:19].replace("T", " ")
            operation = entry.get("operation") or ""
            field = entry.get("field") or ""
            from_val = str(entry.get("from") or "")
            to_val = str(entry.get("to") or "")

            for col, val in enumerate([created_at, nickname, operation, field, from_val, to_val]):
                item = QTableWidgetItem(val)
                item.setToolTip(val)
                self._table.setItem(row, col, item)


class TermsTab(QWidget):

    _PAGE_SIZE = 50

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._workers: list[ApiWorker] = []
        self._terms: list[dict] = []
        self._project_id: int | None = None
        self._gen = 0
        self._page = 1
        self._total = 0
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
        self._history_btn = QPushButton("查看历史")
        self._history_btn.clicked.connect(self._view_history)
        self._history_btn.setEnabled(False)
        for btn in (self._refresh_btn, self._add_btn, self._edit_btn, self._del_btn, self._history_btn):
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

        # 翻页栏
        page_bar = QHBoxLayout()
        self._prev_btn = QPushButton("< 上页")
        self._prev_btn.setEnabled(False)
        self._prev_btn.clicked.connect(self._go_prev)
        self._page_label = QLabel("第 1 页")
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._next_btn = QPushButton("下页 >")
        self._next_btn.setEnabled(False)
        self._next_btn.clicked.connect(self._go_next)
        page_bar.addStretch()
        page_bar.addWidget(self._prev_btn)
        page_bar.addWidget(self._page_label)
        page_bar.addWidget(self._next_btn)
        page_bar.addStretch()
        layout.addLayout(page_bar)

    def _on_project_changed(self, project):
        self._project_id = project.get("id") if project else None
        self._page = 1
        self._total = 0
        self._table.setRowCount(0)
        self._terms = []
        self._update_page_controls()
        if self._project_id:
            self.load_terms()

    def load_terms(self):
        if not self._project_id:
            return
        config = self._ctx.config
        pid = self._project_id
        page = self._page
        self._gen += 1
        gen = self._gen

        def _fetch():
            api = ParatranzTermsAPI(token=config.token, config=config)
            return api.list_terms(pid, page=page, page_size=self._PAGE_SIZE)

        def _on_done(resp):
            if self._gen != gen:
                return
            terms = _extract_list(resp)
            total = resp.get("rowCount", 0) if isinstance(resp, dict) else 0
            self._on_loaded(terms, total)

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
        w.error.connect(lambda e: QMessageBox.warning(self, "加载失败", e))
        w.start()
        self._workers.append(w)

    def _on_loaded(self, terms: list, total: int):
        self._terms = terms
        self._total = total
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
        self._update_page_controls()

    def _update_page_controls(self):
        total_pages = max(1, math.ceil(self._total / self._PAGE_SIZE)) if self._total else 1
        self._page_label.setText(f"第 {self._page} 页 / 共 {self._total} 条")
        self._prev_btn.setEnabled(self._page > 1)
        self._next_btn.setEnabled(self._page < total_pages)

    def _go_prev(self):
        if self._page > 1:
            self._page -= 1
            self.load_terms()

    def _go_next(self):
        total_pages = max(1, math.ceil(self._total / self._PAGE_SIZE)) if self._total else 1
        if self._page < total_pages:
            self._page += 1
            self.load_terms()

    def _reset_and_load(self):
        """增删后回到第 1 页重新加载。"""
        self._page = 1
        self.load_terms()

    def _selected_term(self) -> dict | None:
        rows = self._table.selectedItems()
        return rows[0].data(Qt.ItemDataRole.UserRole) if rows else None

    def _on_selection_changed(self):
        has = self._selected_term() is not None
        self._edit_btn.setEnabled(has)
        self._del_btn.setEnabled(has)
        self._history_btn.setEnabled(has)

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
        w.result.connect(lambda _: self._reset_and_load())
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
        w.result.connect(lambda _: self._reset_and_load())
        w.error.connect(lambda e: QMessageBox.critical(self, "删除失败", e))
        w.start()
        self._workers.append(w)

    def _view_history(self):
        t = self._selected_term()
        if not t:
            return
        config = self._ctx.config
        pid = self._project_id
        tid = t.get("id")
        term_name = t.get("term", str(tid))

        self._history_btn.setEnabled(False)
        self._history_btn.setText("加载中…")

        def _fetch():
            api = ParatranzHistoryAPI(token=config.token, config=config)
            result = api.get_term_history(pid, tid)
            if isinstance(result, dict):
                return result.get("data") or result.get("results") or result.get("items") or []
            if isinstance(result, list):
                return result
            return []

        def _on_done(history):
            self._history_btn.setEnabled(True)
            self._history_btn.setText("查看历史")
            dlg = TermHistoryDialog(term_name, history, parent=self)
            dlg.exec()

        def _on_error(e):
            self._history_btn.setEnabled(True)
            self._history_btn.setText("查看历史")
            QMessageBox.warning(self, "加载失败", e)

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)
