"""润色结果预览对话框，支持逐条接受/拒绝。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QSignalBlocker, Qt
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

from transbridge.application.translation.entry_alias import ai_entry_id
from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, SemanticState

from ._theme_support import AiThemeBinding

if TYPE_CHECKING:
    from transbridge.ai_translator.post_processor.polisher import PolishResult
    from transbridge.converter.translation_entry import TranslationEntry

_COL_ORIGINAL = 0
_COL_CURRENT = 1
_COL_ISSUES = 2
_COL_REFINED = 3
_COL_POLISHED = 4
_COL_DECISION = 5
_COL_SELECTION = 6

_STATUS_ACCEPTED = "accepted"
_STATUS_REJECTED = "rejected"
_STATUS_PENDING = "pending"
_STATUS_FAILED = "failed"


class _PolishPreviewDialog(QDialog):
    """润色结果预览对话框。

    展示原文、当前译文、检测问题、纠错候选、最终候选、裁决和采用选择。
    每行可反复选择采用候选或保留原译文；生成失败的条目不可采用。
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

    def keyPressEvent(self, event) -> None:
        # Reviewing a cell must not activate a bulk choice through QDialog's
        # implicit default button. Explicitly focused buttons keep Enter support.
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._table.hasFocus():
            event.accept()
            return
        super().keyPressEvent(event)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── 工具栏 ──────────────────────────────────────────────────────────
        toolbar = QHBoxLayout()

        self._accept_all_btn = QPushButton("全部采用候选")
        self._accept_all_btn.clicked.connect(self._on_accept_all)
        toolbar.addWidget(self._accept_all_btn)

        self._reject_all_btn = QPushButton("全部保留原译文")
        self._reject_all_btn.clicked.connect(self._on_reject_all)
        toolbar.addWidget(self._reject_all_btn)

        toolbar.addStretch()

        self._stats_lbl = QLabel()
        toolbar.addWidget(self._stats_lbl)

        layout.addLayout(toolbar)
        hint = QLabel("在“采用候选”列勾选采用、取消勾选保留原译文；确认应用前可随时更改。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # ── 表格 ────────────────────────────────────────────────────────────
        self._table = QTableWidget()
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels([
            "原文",
            "当前译文",
            "检测问题",
            "纠错候选",
            "最终候选",
            "裁决",
            "采用候选",
        ])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(_COL_SELECTION, QHeaderView.ResizeMode.Fixed)
        self._resize_choice_column()
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setTabKeyNavigation(False)
        self._table.verticalHeader().setVisible(False)
        self._table.cellClicked.connect(self._on_cell_clicked)
        self._table.itemChanged.connect(self._on_choice_changed)
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
            result = self._results.get(ai_entry_id(entry))
            if result is None:
                self._row_status[row] = _STATUS_FAILED
                self._add_row(
                    row,
                    entry.original,
                    entry.translation or "",
                    "未生成结果",
                    "",
                    entry.translation or "",
                    "failed",
                    True,
                )
                continue

            failed = result.confidence <= 0
            self._row_status[row] = _STATUS_FAILED if failed else _STATUS_PENDING
            issues = "\n".join(f"[{issue.severity}] {issue.message}" for issue in getattr(result, "issues", ())) or "无"
            decision = f"{getattr(result, 'verdict', 'pending')} ({result.confidence:.2f})"
            if result.note:
                decision = f"{decision}\n{result.note}"
            self._add_row(
                row,
                entry.original or "",
                entry.translation or "",
                issues,
                getattr(result, "refined_translation", None) or entry.translation or "",
                result.polished_translation or entry.translation or "",
                decision,
                failed,
            )

        self._update_stats()

    def _add_row(
        self,
        row: int,
        original: str,
        current: str,
        issues: str,
        refined: str,
        polished: str,
        decision: str,
        failed: bool,
    ):
        items = []
        for col, text in enumerate([original, current, issues, refined, polished, decision]):
            item = QTableWidgetItem(text)
            item.setToolTip(text)
            if col == _COL_POLISHED and not failed:
                item.setData(Qt.ItemDataRole.UserRole, _STATUS_PENDING)
            items.append(item)

        for col, item in enumerate(items):
            self._table.setItem(row, col, item)
        choice = QTableWidgetItem()
        choice.setFlags(choice.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        if failed:
            choice.setFlags(choice.flags() & ~Qt.ItemFlag.ItemIsEnabled & ~Qt.ItemFlag.ItemIsUserCheckable)
            choice.setToolTip("未生成有效候选，将保留原译文。")
        else:
            choice.setToolTip("勾选采用候选，取消勾选保留原译文；可随时更改。")
        with QSignalBlocker(self._table):
            self._table.setItem(row, _COL_SELECTION, choice)
        self._refresh_row(row)

    def _on_cell_clicked(self, row: int, col: int):
        if col != _COL_POLISHED:
            return
        if self._row_status.get(row) == _STATUS_FAILED:
            return

        current_status = self._row_status.get(row, _STATUS_PENDING)
        self._set_row_status(row, _STATUS_REJECTED if current_status == _STATUS_ACCEPTED else _STATUS_ACCEPTED)
        self._update_stats()

    def _on_choice_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != _COL_SELECTION or item.checkState() == Qt.CheckState.PartiallyChecked:
            return
        self._set_row_status(
            item.row(), _STATUS_ACCEPTED if item.checkState() == Qt.CheckState.Checked else _STATUS_REJECTED
        )
        self._update_stats()

    def _set_row_status(self, row: int, status: str) -> None:
        if self._row_status.get(row) == _STATUS_FAILED:
            return
        self._row_status[row] = status
        self._table.item(row, _COL_POLISHED).setData(Qt.ItemDataRole.UserRole, status)
        self._refresh_row(row)

    def _on_accept_all(self):
        for row in range(self._table.rowCount()):
            self._set_row_status(row, _STATUS_ACCEPTED)
        self._update_stats()

    def _on_reject_all(self):
        for row in range(self._table.rowCount()):
            self._set_row_status(row, _STATUS_REJECTED)
        self._update_stats()

    def _update_stats(self):
        accepted = sum(1 for s in self._row_status.values() if s == _STATUS_ACCEPTED)
        rejected = sum(1 for s in self._row_status.values() if s == _STATUS_REJECTED)
        pending = sum(1 for s in self._row_status.values() if s == _STATUS_PENDING)
        failed = sum(1 for s in self._row_status.values() if s == _STATUS_FAILED)
        total = len(self._row_status)
        self._stats_lbl.setText(f"采用 {accepted} · 保留 {rejected} · 待选 {pending} · 失败 {failed} · 共 {total} 条")
        self._stats_lbl.setAccessibleName("润色选择状态")
        self._stats_lbl.setAccessibleDescription(self._stats_lbl.text())

    def _apply_theme(self, binding: AiThemeBinding) -> None:
        self._resize_choice_column()
        for row in range(self._table.rowCount()):
            self._refresh_row(row, binding)

    def _resize_choice_column(self) -> None:
        # Reserve checkbox and longest label space so choosing an option never moves the click target.
        self._table.setColumnWidth(_COL_SELECTION, self._table.fontMetrics().horizontalAdvance("保留原译文") + 48)

    def _refresh_row(self, row: int, binding: AiThemeBinding | None = None) -> None:
        status = self._row_status[row]
        choice = self._table.item(row, _COL_SELECTION)
        if choice is not None:
            with QSignalBlocker(self._table):
                choice.setText(
                    {
                        _STATUS_ACCEPTED: "采用候选",
                        _STATUS_REJECTED: "保留原译文",
                        _STATUS_PENDING: "待选择",
                        _STATUS_FAILED: "生成失败",
                    }[status]
                )
                if status != _STATUS_FAILED:
                    choice.setCheckState(
                        {
                            _STATUS_ACCEPTED: Qt.CheckState.Checked,
                            _STATUS_REJECTED: Qt.CheckState.Unchecked,
                            _STATUS_PENDING: Qt.CheckState.PartiallyChecked,
                        }[status]
                    )
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
                polish_result = self._results.get(ai_entry_id(entry))
                if polish_result:
                    result[ai_entry_id(entry)] = polish_result.polished_translation
                else:
                    result[ai_entry_id(entry)] = None
            else:
                result[ai_entry_id(entry)] = None
        return result
