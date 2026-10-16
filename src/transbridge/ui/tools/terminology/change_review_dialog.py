"""Complete, searchable semantic changes shared by publication review and history."""

from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from transbridge.application.terminology.models import CanonicalChange, CanonicalDiff, ChangeType, TermDecision

from .async_row_filter import AsyncRowFilter
from .filtered_rows_model import FilteredRowsModel
from .row_search import SearchDocument

_CHANGE_LABELS = {
    ChangeType.ADDED: "新增",
    ChangeType.SUPPRESSED: "停用",
    ChangeType.TRANSLATION_CHANGED: "调整译名",
    ChangeType.ORIGINAL_REPLACED: "替换原词",
    ChangeType.SCOPE_CHANGED: "调整范围",
    ChangeType.ATTRIBUTES_CHANGED: "调整属性",
    ChangeType.CONFLICT_STATUS_CHANGED: "冲突处理变化",
    ChangeType.REENABLED: "重新启用",
    ChangeType.EVIDENCE_ONLY: "证据更新",
}
_STATUS_LABELS = {
    "adopted": "已采用",
    "manual_confirmed": "人工确认",
    "review_required": "待复核",
    "unresolved": "待决定",
    "unified": "已统一",
    "plugin_exception": "按插件分别使用",
    "ignored": "已排除",
    "absent": "不存在",
}


def _scope(decision: TermDecision | None) -> str:
    if decision is None:
        return "—"
    return "整个工程" if decision.scope.plugin_id is None else f"插件：{decision.scope.plugin_id}"


def _status(decision: TermDecision | None, fallback: str = "absent") -> str:
    if decision is None:
        return _STATUS_LABELS.get(fallback, fallback)
    status = _STATUS_LABELS.get(decision.status.value, decision.status.value)
    return f"已停用 · {status}" if decision.suppressed else status


def _change_values(change: CanonicalChange) -> tuple[str, ...]:
    before, after = change.before, change.after
    details = dict(change.details)
    original = after.original if after is not None else (before.original if before is not None else change.term_id)
    if before is not None and after is not None and before.original != after.original:
        original = f"{before.original} → {after.original}"
    change_type = "移除" if before is not None and after is None else _CHANGE_LABELS[change.change_type]
    return (
        change_type,
        original,
        "—" if before is None else before.translation,
        "—" if after is None else after.translation,
        _scope(before),
        _scope(after),
        _status(before, details.get("before_status", "absent")),
        _status(after, details.get("after_status", "absent")),
    )


def _decision_details(decision: TermDecision | None) -> str:
    if decision is None:
        return "不存在"
    return "\n".join((
        f"原词：{decision.original}",
        f"译名：{decision.translation}",
        f"范围：{_scope(decision)}",
        f"状态：{_status(decision)}",
        f"其他译名：{'、'.join(decision.variants) or '无'}",
        f"备注：{decision.notes or '无'}",
        f"证据：{'、'.join(decision.evidence_ids) or '无'}",
    ))


def _change_details(change: CanonicalChange) -> str:
    detail_labels = {"before_status": "原冲突状态", "after_status": "新冲突状态", "replacement_of": "替换自"}
    details = "\n".join(
        f"{detail_labels.get(key, key)}：{_STATUS_LABELS.get(value, value)}" for key, value in change.details
    )
    return (
        f"{_change_values(change)[0]} · {'人工调整' if change.manual else '构建结果'}\n"
        f"术语标识：{change.term_id}\n\n"
        f"变更前\n{_decision_details(change.before)}\n\n"
        f"变更后\n{_decision_details(change.after)}" + (f"\n\n{details}" if details else "")
    )


class _ChangesModel(QAbstractTableModel):
    headers = ("变化", "原词", "原译名", "新译名", "原范围", "新范围", "原状态", "新状态")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.changes: tuple[CanonicalChange, ...] = ()

    def set_diff(self, comparison: CanonicalDiff) -> None:
        self.beginResetModel()
        self.changes = comparison.changes
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.changes)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.changes):
            return None
        change = self.changes[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return change
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return _change_values(change)[index.column()]
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.headers[section]
        return None


class CanonicalDiffView(QWidget):
    """Virtual table over the complete immutable diff; search includes hidden details."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.summary = QLabel(self)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("搜索全部变化：原词、译名、插件、状态或备注")
        self.search.setAccessibleName("搜索全部术语变化")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.model = _ChangesModel(self)
        self.filtered = FilteredRowsModel(self.model, self)
        splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.table = QTableView(splitter)
        self.table.setAccessibleName("术语实际变化")
        self.table.setProperty("tbComponentKind", "table")
        self.table.setModel(self.filtered)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for column in (1, 2, 3):
            self.table.setColumnWidth(column, 150)
        self.details = QPlainTextEdit(splitter)
        self.details.setReadOnly(True)
        self.details.setAccessibleName("选中变化的完整前后内容")
        self.details.setPlaceholderText("选择一项变化，查看完整前后内容。")
        splitter.setSizes([310, 170])
        layout.addWidget(splitter, 1)
        self.count = QLabel(self)
        self.count.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.count)
        self.table.selectionModel().currentChanged.connect(self._selected)
        self._search = AsyncRowFilter(self)
        self._search.matches_changed.connect(self._matches_ready)
        self._search.status_changed.connect(self._search_status)
        self.search.textChanged.connect(self._filter)

    def set_comparison(self, comparison: CanonicalDiff) -> None:
        self.model.set_diff(comparison)
        parent = comparison.parent_version_id or "空术语库"
        self.summary.setText(
            f"比较方向：{parent} → {comparison.target_version_id} · 共 {len(comparison.changes)} 项变化"
        )
        self._search.set_rows(self.model.changes, _search_document)

    def _filter(self, text: str) -> None:
        self._search.set_query(text)

    def _matches_ready(self, matches) -> None:
        selected = self.filtered.mapToSource(self.table.currentIndex())
        self.filtered.set_matches(matches)
        count = self.filtered.rowCount()
        self.count.setText(f"显示 {count} / {self.model.rowCount()} 项变化")
        self.details.clear()
        if count:
            index = self.filtered.mapFromSource(selected)
            self.table.setCurrentIndex(index if index.isValid() else self.filtered.index(0, 0))
            self._selected(self.table.currentIndex())
        elif self.model.rowCount():
            self.details.setPlainText("没有匹配的变化；清空搜索可查看全部。")
        else:
            self.details.setPlainText("两个版本的术语内容没有变化。")

    def _search_status(self, pending, error) -> None:
        if pending or error:
            self.count.setText(error or "正在搜索全部术语变化…")
        else:
            self.count.setText(f"显示 {self.filtered.rowCount()} / {self.model.rowCount()} 项变化")

    def close_search(self) -> None:
        self._search.close()

    def _selected(self, current, _previous=None) -> None:
        change = current.data(Qt.ItemDataRole.UserRole)
        self.details.setPlainText("" if change is None else _change_details(change))


class ChangeReviewDialog(QDialog):
    def __init__(self, comparison: CanonicalDiff, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("检查发布影响")
        self.resize(1120, 700)
        layout = QVBoxLayout(self)
        explanation = QLabel("确认后会发布本次审阅的术语内容；若草稿或工程输入变化，需要重新检查影响。", self)
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.changes = CanonicalDiffView(self)
        self.finished.connect(self.changes.close_search)
        self.changes.set_comparison(comparison)
        layout.addWidget(self.changes, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        self.publish_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.publish_button.setText("确认发布")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


__all__ = ["CanonicalDiffView", "ChangeReviewDialog"]


def _search_document(change) -> SearchDocument:
    return SearchDocument((" ".join(_change_values(change)) + " " + _change_details(change),))
