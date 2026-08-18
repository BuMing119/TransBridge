"""
MembersTab: 成员管理标签页。
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QMessageBox, QDialog, QFormLayout,
    QLineEdit, QComboBox, QTextEdit, QLabel,
)
from PyQt6.QtCore import Qt

from transbridge.paratranz.api.paratranz_members_api import ParatranzMembersAPI
from ..workers import ApiWorker

_PERM_LABELS = {1: "翻译者", 2: "校对者", 3: "管理员", 4: "所有者"}


def _extract_list(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("data", "results", "items"):
            if isinstance(data.get(k), list):
                return data[k]
    return []


class MembersTab(QWidget):

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._workers: list[ApiWorker] = []
        self._project_id: int | None = None
        self._gen = 0
        self._init_ui()
        ctx.project_selected.connect(self._on_project_changed)

    def _init_ui(self):
        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        self._refresh_btn = QPushButton("刷新")
        self._refresh_btn.clicked.connect(self.load_members)
        self._add_btn = QPushButton("添加成员")
        self._add_btn.clicked.connect(self._add_member)
        self._remove_btn = QPushButton("移除成员")
        self._remove_btn.setStyleSheet("color: red;")
        self._remove_btn.clicked.connect(self._remove_member)
        self._remove_btn.setEnabled(False)
        for btn in (self._refresh_btn, self._add_btn, self._remove_btn):
            toolbar.addWidget(btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(
            ["昵称", "用户名", "权限", "PP 贡献", "翻译数", "编辑数", "备注"]
        )
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        for i in (2, 3, 4, 5):
            self._table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.itemSelectionChanged.connect(
            lambda: self._remove_btn.setEnabled(
                bool(self._table.selectedItems()) and self._ctx.is_admin()
            )
        )
        layout.addWidget(self._table, stretch=1)

    def _on_project_changed(self, project):
        self._project_id = project.get("id") if project else None
        self._table.setRowCount(0)
        if self._project_id:
            self.load_members()

    def load_members(self):
        if not self._project_id:
            return
        config = self._ctx.config
        pid = self._project_id
        self._gen += 1
        gen = self._gen

        def _fetch():
            api = ParatranzMembersAPI(token=config.token, config=config)
            return _extract_list(api.list_members(pid))

        def _on_done(members):
            if self._gen != gen:
                return
            # 缓存到 project dict 以供权限判断使用
            if self._ctx.current_project:
                self._ctx.current_project["_members"] = members
            self._fill_table(members)

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
        w.error.connect(lambda e: QMessageBox.warning(self, "加载失败", e))
        w.start()
        self._workers.append(w)

    def _fill_table(self, members: list):
        self._table.setRowCount(len(members))
        for row, m in enumerate(members):
            user = m.get("user") or {}
            perm = m.get("permission", 1)
            cells = [
                user.get("nickname", "—") if isinstance(user, dict) else "—",
                user.get("username", "—") if isinstance(user, dict) else "—",
                _PERM_LABELS.get(perm, str(perm)),
                str(m.get("totalPoints", "—")),
                str(m.get("translated", "—")),
                str(m.get("edited", "—")),
                m.get("note", "") or "",
            ]
            for col, val in enumerate(cells):
                item = QTableWidgetItem(val)
                item.setData(Qt.ItemDataRole.UserRole, m)
                self._table.setItem(row, col, item)

    def _selected_member(self) -> dict | None:
        rows = self._table.selectedItems()
        return rows[0].data(Qt.ItemDataRole.UserRole) if rows else None

    def _add_member(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("添加成员")
        layout = QFormLayout(dlg)
        uid_input = QLineEdit()
        uid_input.setPlaceholderText("用户 ID（整数）")
        perm_combo = QComboBox()
        for val, name in _PERM_LABELS.items():
            if val < 4:
                perm_combo.addItem(name, val)
        layout.addRow("用户 ID *", uid_input)
        layout.addRow("权限", perm_combo)
        btn_row = QHBoxLayout()
        ok_btn = QPushButton("添加")
        cancel_btn = QPushButton("取消")
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addRow(btn_row)
        ok_btn.clicked.connect(dlg.accept)
        cancel_btn.clicked.connect(dlg.reject)
        if not dlg.exec():
            return
        try:
            uid = int(uid_input.text().strip())
        except ValueError:
            QMessageBox.warning(self, "输入错误", "用户 ID 必须是整数")
            return
        perm = perm_combo.currentData()
        config = self._ctx.config
        pid = self._project_id

        def _add():
            api = ParatranzMembersAPI(token=config.token, config=config)
            return api.add_member(pid, uid, perm)

        w = ApiWorker(_add)
        w.result.connect(lambda _: self.load_members())
        w.error.connect(lambda e: QMessageBox.critical(self, "添加失败", e))
        w.start()
        self._workers.append(w)

    def _remove_member(self):
        m = self._selected_member()
        if not m:
            return
        user = m.get("user") or {}
        name = user.get("nickname", str(m.get("uid", ""))) if isinstance(user, dict) else ""
        reply = QMessageBox.question(
            self, "确认移除",
            f"确定要移除成员「{name}」吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        config = self._ctx.config
        pid = self._project_id
        member_id = m.get("id")

        def _remove():
            api = ParatranzMembersAPI(token=config.token, config=config)
            return api.delete_member(pid, member_id)

        w = ApiWorker(_remove)
        w.result.connect(lambda _: self.load_members())
        w.error.connect(lambda e: QMessageBox.critical(self, "移除失败", e))
        w.start()
        self._workers.append(w)
