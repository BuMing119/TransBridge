"""Scalable table and filtering models for explicit synchronization decisions."""

from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt

from .plan_view import OperationKind
from .sync_review_direction import sync_review_direction
from .sync_review_view import SyncReviewRow


class SyncReviewModel(QAbstractTableModel):
    HEADERS = ("本地标识", "本地原文", "本地译文", "待处理原因", "本次处理")

    def __init__(self, rows: tuple[SyncReviewRow, ...], parent=None, *, kind=OperationKind.DOWNLOAD) -> None:
        super().__init__(parent)
        self.rows = rows
        self._direction = sync_review_direction(kind)
        self._decisions = {
            row.row_id: row.decision
            for row in rows
            if row.decision == self._direction.keep_choice
            or (
                row.can_select_remote
                and any(item.candidate_id == row.decision and item.selectable for item in row.candidates)
            )
        }
        self.search_text = tuple(
            "\n".join(
                (row.key, row.original, row.translation, row.context, row.reason)
                + tuple(
                    text
                    for candidate in row.candidates
                    for text in (candidate.label, candidate.original, candidate.translation, candidate.context)
                )
            ).casefold()
            for row in rows
        )

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 - Qt override
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802 - Qt override
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return super().headerData(section, orientation, role)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        if role not in (
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.ToolTipRole,
            Qt.ItemDataRole.AccessibleTextRole,
        ):
            return None
        decision = self._decisions.get(row.row_id, "")
        choice = self._direction.pending_label
        if decision == self._direction.keep_choice:
            choice = self._direction.keep_label
        elif decision:
            candidate = next((item for item in row.candidates if item.candidate_id == decision), None)
            choice = self._direction.candidate_prefix if candidate else self._direction.pending_label
        if role in (Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.AccessibleTextRole):
            return f"{row.original}\n{row.translation}\n{choice}"
        return (row.key, row.original, row.translation, row.reason, choice)[index.column()]

    def decision(self, row: int) -> str:
        return self._decisions.get(self.rows[row].row_id, "")

    def decisions(self) -> dict[str, str]:
        return dict(self._decisions)

    def set_decision(self, indices: tuple[int, ...], value: str) -> None:
        for index in indices:
            row = self.rows[index]
            if (
                value
                and value != self._direction.keep_choice
                and (
                    not row.can_select_remote
                    or not any(item.candidate_id == value and item.selectable for item in row.candidates)
                )
            ):
                raise ValueError("cloud selection is not available for this review row")
        for index in indices:
            row = self.rows[index]
            if value:
                self._decisions[row.row_id] = value
            else:
                self._decisions.pop(row.row_id, None)
        if indices:
            self.dataChanged.emit(self.index(min(indices), 0), self.index(max(indices), 4))


class SyncReviewFilter(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.search = ""
        self.reason = ""
        self.status = ""

    def set_filters(self, *, search: str, reason: str, status: str) -> None:
        self.search, self.reason, self.status = search.casefold().strip(), reason, status
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):  # noqa: N802 - Qt override
        model = self.sourceModel()
        row = model.rows[source_row]
        decision = model.decision(source_row)
        if self.reason and row.reason != self.reason:
            return False
        if self.status == "pending" and decision:
            return False
        if self.status == "decided" and not decision:
            return False
        return not self.search or self.search in model.search_text[source_row]
