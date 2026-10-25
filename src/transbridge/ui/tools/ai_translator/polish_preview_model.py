"""Pure preview projection and a virtual, incrementally counted choice model."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal

from transbridge.application.translation.entry_alias import ai_entry_id

ACCEPTED, REJECTED, PENDING, FAILED = "accepted", "rejected", "pending", "failed"
CHOICE_LABELS = {ACCEPTED: "采用候选", REJECTED: "保留原译文", PENDING: "待选择", FAILED: "生成失败"}
CHECK_STATES = {
    ACCEPTED: Qt.CheckState.Checked,
    REJECTED: Qt.CheckState.Unchecked,
    PENDING: Qt.CheckState.PartiallyChecked,
}


@dataclass(frozen=True)
class PreviewRow:
    entry_id: str
    values: tuple[str, ...]
    candidate: str
    failed: bool


@dataclass(frozen=True)
class PreviewProjection:
    rows: tuple[PreviewRow, ...]
    failed_count: int


def build_preview(request) -> PreviewProjection:
    entries, results = request
    rows = []
    failed_count = 0
    for entry in entries:
        entry_id = ai_entry_id(entry)
        result = results.get(entry_id)
        failed = result is None or result.confidence <= 0
        failed_count += failed
        current = entry.translation or ""
        if result is None:
            values = (entry.original or "", current, "未生成结果", "", current, "failed")
            candidate = ""
        else:
            issues = "\n".join(f"[{issue.severity}] {issue.message}" for issue in getattr(result, "issues", ())) or "无"
            decision = f"{getattr(result, 'verdict', 'pending')} ({result.confidence:.2f})"
            if result.note:
                decision += f"\n{result.note}"
            candidate = result.polished_translation
            values = (
                entry.original or "",
                current,
                issues,
                getattr(result, "refined_translation", "") or "",
                candidate or current,
                decision,
            )
        rows.append(PreviewRow(entry_id, values, candidate, failed))
    return PreviewProjection(tuple(rows), failed_count)


class PolishPreviewModel(QAbstractTableModel):
    choices_changed = pyqtSignal()
    headers = ("原文", "当前译文", "检测问题", "纠错候选", "最终候选", "裁决", "采用候选")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = ()
        self.counts = Counter()
        self._default = PENDING
        self._choices = {}
        self._backgrounds = {}

    def set_projection(self, projection):
        self.beginResetModel()
        self.rows = projection.rows
        self._default = PENDING
        self._choices.clear()
        self.counts = Counter({FAILED: projection.failed_count, PENDING: len(self.rows) - projection.failed_count})
        self.endResetModel()
        self.choices_changed.emit()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.headers[section]
        return None

    def status(self, row):
        return FAILED if self.rows[row].failed else self._choices.get(row, self._default)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        row, column = index.row(), index.column()
        status = self.status(row)
        if role == Qt.ItemDataRole.DisplayRole:
            return CHOICE_LABELS[status] if column == 6 else self.rows[row].values[column][:240]
        if role == Qt.ItemDataRole.ToolTipRole:
            if column != 6:
                text = self.rows[row].values[column]
                return text if len(text) <= 2048 else text[:2048] + "\n[显示已截断，完整候选仍保留在任务结果中]"
            return (
                "未生成有效候选，将保留原译文。"
                if status == FAILED
                else "勾选采用候选，取消勾选保留原译文；可随时更改。"
            )
        if role == Qt.ItemDataRole.CheckStateRole and column == 6:
            return CHECK_STATES.get(status)
        if role == Qt.ItemDataRole.UserRole and column == 4:
            return status
        if role == Qt.ItemDataRole.BackgroundRole and column == 4:
            return self._backgrounds.get(status)
        return None

    def flags(self, index):
        flags = super().flags(index)
        if index.isValid() and index.column() == 6:
            if self.rows[index.row()].failed:
                return flags & ~Qt.ItemFlag.ItemIsEnabled
            return flags | Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if index.isValid() and index.column() == 6 and role == Qt.ItemDataRole.CheckStateRole:
            checked = value == Qt.CheckState.Checked or value == Qt.CheckState.Checked.value
            return self.set_status(index.row(), ACCEPTED if checked else REJECTED)
        return False

    def set_status(self, row, status):
        before = self.status(row)
        if before == FAILED or before == status:
            return False
        self._choices[row] = status
        self.counts[before] -= 1
        self.counts[status] += 1
        self.dataChanged.emit(self.index(row, 4), self.index(row, 6))
        self.choices_changed.emit()
        return True

    def set_all(self, status):
        self._default = status
        self._choices.clear()
        failed = self.counts[FAILED]
        self.counts = Counter({FAILED: failed, status: len(self.rows) - failed})
        self._repaint()
        self.choices_changed.emit()

    def set_backgrounds(self, backgrounds):
        self._backgrounds = backgrounds
        self._repaint()

    def _repaint(self):
        if self.rows:
            self.dataChanged.emit(self.index(0, 4), self.index(len(self.rows) - 1, 6))

    def results(self):
        return {row.entry_id: row.candidate if self.status(i) == ACCEPTED else None for i, row in enumerate(self.rows)}
