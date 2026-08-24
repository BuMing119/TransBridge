"""润色结果预览对话框，支持逐条接受/拒绝。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, SemanticState

from ._theme_support import AiThemeBinding

if TYPE_CHECKING:
    from transbridge.ai_translator.post_processor.polisher import PolishResult
    from transbridge.converter.translation_entry import TranslationEntry

_COL_ORIGINAL = 0
_COL_CURRENT = 1
_COL_POLISHED = 2

_STATUS_ACCEPTED = "accepted"
_STATUS_REJECTED = "rejected"
_STATUS_PENDING = "pending"


class _PolishPreviewDialog(QDialog):
    """润色结果预览对话框。

    三列对比：原文 | 原译文 | 润色结果。
    每行可接受（使用润色结果）或拒绝（保留原译文）。
    """

    def __init__(
        self,
        entries: list[TranslationEntry],
        results: dict[str, PolishResult],
        parent=None,
        *,
        theme_view: ThemeView | None = None,
    ):
        super().__init__(parent)
        self._entries = entries
        self._results = results
        self._row_status: dict[int, str] = {}  # row_index → status
        self.setWindowTitle("润色结果预览")
        self.resize(900, 550)
        self._init_ui()
        self._populate()
        self._theme_binding = AiThemeBinding(self, theme_view, self._apply_theme)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── 工具栏 ──────────────────────────────────────────────────────────
        toolbar = QHBoxLayout()

        self._accept_all_btn = QPushButton("✓ 全部接受")
        self._accept_all_btn.clicked.connect(self._on_accept_all)
        toolbar.addWidget(self._accept_all_btn)

        self._reject_all_btn = QPushButton("✗ 全部拒绝")
        self._reject_all_btn.clicked.connect(self._on_reject_all)
        toolbar.addWidget(self._reject_all_btn)

        toolbar.addStretch()

        self._stats_lbl = QLabel()
        toolbar.addWidget(self._stats_lbl)

        layout.addLayout(toolbar)

        # ── 表格 ────────────────────────────────────────────────────────────
        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["原文", "原译文", "润色结果"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table)

        # ── 底部按钮 ────────────────────────────────────────────────────────
        bottom = QHBoxLayout()
        bottom.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        bottom.addWidget(cancel_btn)

        self._apply_btn = QPushButton("确认应用")
        ComponentStyle.apply_static(self._apply_btn, ComponentKind.BUTTON)
        ComponentStyle.apply_state(self._apply_btn, SemanticState.PRIMARY)
        self._apply_btn.clicked.connect(self._on_apply)
        bottom.addWidget(self._apply_btn)
        layout.addLayout(bottom)

    def _populate(self):
        self._table.setRowCount(len(self._entries))
        self._row_status.clear()

        for row, entry in enumerate(self._entries):
            result = self._results.get(entry.id)
            if result is None:
                self._row_status[row] = _STATUS_REJECTED
                self._add_row(row, entry.original, entry.translation or "", entry.translation or "", True)
                continue

            self._row_status[row] = _STATUS_PENDING
            self._add_row(
                row,
                entry.original or "",
                entry.translation or "",
                result.polished_translation or entry.translation or "",
                False,
            )

        self._update_stats()

    def _add_row(self, row: int, original: str, current: str, polished: str, failed: bool):
        items = []
        for col, text in enumerate([original, current, polished]):
            item = QTableWidgetItem(text)
            item.setToolTip(text)
            if col == _COL_POLISHED and not failed:
                item.setData(Qt.ItemDataRole.UserRole, _STATUS_PENDING)
            items.append(item)

        self._table.setItem(row, _COL_ORIGINAL, items[_COL_ORIGINAL])
        self._table.setItem(row, _COL_CURRENT, items[_COL_CURRENT])
        self._table.setItem(row, _COL_POLISHED, items[_COL_POLISHED])

        # 点击润色列切换接受/拒绝
        if not failed:
            self._table.cellClicked.connect(self._on_cell_clicked)

    def _on_cell_clicked(self, row: int, col: int):
        if col != _COL_POLISHED:
            return
        if self._row_status.get(row) == _STATUS_REJECTED:
            return

        current_status = self._row_status.get(row, _STATUS_PENDING)
        if current_status == _STATUS_ACCEPTED:
            self._row_status[row] = _STATUS_REJECTED
            self._table.item(row, _COL_POLISHED).setData(Qt.ItemDataRole.UserRole, _STATUS_REJECTED)
        else:
            self._row_status[row] = _STATUS_ACCEPTED
            self._table.item(row, _COL_POLISHED).setData(Qt.ItemDataRole.UserRole, _STATUS_ACCEPTED)

        self._refresh_row(row)
        self._update_stats()

    def _on_accept_all(self):
        for row in range(self._table.rowCount()):
            if self._row_status.get(row) != _STATUS_REJECTED:
                self._row_status[row] = _STATUS_ACCEPTED
                self._table.item(row, _COL_POLISHED).setData(Qt.ItemDataRole.UserRole, _STATUS_ACCEPTED)
                self._refresh_row(row)
        self._update_stats()

    def _on_reject_all(self):
        for row in range(self._table.rowCount()):
            self._row_status[row] = _STATUS_REJECTED
            if self._table.item(row, _COL_POLISHED):
                self._table.item(row, _COL_POLISHED).setData(Qt.ItemDataRole.UserRole, _STATUS_REJECTED)
            self._refresh_row(row)
        self._update_stats()

    def _update_stats(self):
        accepted = sum(1 for s in self._row_status.values() if s == _STATUS_ACCEPTED)
        rejected = sum(1 for s in self._row_status.values() if s == _STATUS_REJECTED)
        pending = sum(1 for s in self._row_status.values() if s == _STATUS_PENDING)
        total = len(self._row_status)
        self._stats_lbl.setText(f"接受: {accepted}  |  拒绝: {rejected}  |  待处理: {pending}  |  共 {total} 条")
        self._stats_lbl.setAccessibleName("润色选择状态")
        self._stats_lbl.setAccessibleDescription(self._stats_lbl.text())

    def _apply_theme(self, binding: AiThemeBinding) -> None:
        for row in range(self._table.rowCount()):
            self._refresh_row(row, binding)

    def _refresh_row(self, row: int, binding: AiThemeBinding | None = None) -> None:
        binding = binding or getattr(self, "_theme_binding", None)
        if binding is None or binding.domain is None:
            return
        key = {
            _STATUS_ACCEPTED: "added",
            _STATUS_REJECTED: "removed",
            _STATUS_PENDING: "changed",
        }.get(self._row_status.get(row), "unchanged")
        brush = binding.domain.diff(key).background
        item = self._table.item(row, _COL_POLISHED)
        if item is not None:
            item.setBackground(brush)

    @property
    def theme_revision(self) -> int:
        return self._theme_binding.revision

    def _on_apply(self):
        pending = sum(1 for s in self._row_status.values() if s == _STATUS_PENDING)
        if pending > 0:
            from PyQt6.QtWidgets import QMessageBox

            reply = QMessageBox.question(
                self,
                "待处理条目",
                f"还有 {pending} 条未处理，未处理的条目将保留原译文。\n\n确定继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.accept()

    def get_results(self) -> dict[str, str | None]:
        """
        获取润色结果映射。

        Returns:
            entry_id → 最终译文（None 表示拒绝润色，保留原译文）
        """
        result: dict[str, str | None] = {}
        for row, entry in enumerate(self._entries):
            status = self._row_status.get(row, _STATUS_REJECTED)
            if status == _STATUS_ACCEPTED:
                polish_result = self._results.get(entry.id)
                if polish_result:
                    result[entry.id] = polish_result.polished_translation
                else:
                    result[entry.id] = None
            else:
                result[entry.id] = None
        return result
