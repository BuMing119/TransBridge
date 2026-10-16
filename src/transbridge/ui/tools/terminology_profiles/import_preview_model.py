"""Virtual import preview with presentation-only search and diagnostic filtering."""

from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal

from transbridge.application.terminology_profiles.import_preview import ImportPreviewOutcome, ImportPreviewRow
from transbridge.ui.tools.terminology.async_row_filter import AsyncRowFilter
from transbridge.ui.tools.terminology.filtered_rows_model import FilteredRowsModel
from transbridge.ui.tools.terminology.row_search import SearchDocument

OUTCOME_LABELS = {
    ImportPreviewOutcome.ADOPTED: "采用来源译名",
    ImportPreviewOutcome.KEPT: "保持项目默认译名",
    ImportPreviewOutcome.SOURCE_CONFLICT: "来源译名冲突，保持默认",
    ImportPreviewOutcome.SCOPE_AMBIGUOUS: "作用域不明确，保持默认",
    ImportPreviewOutcome.SOURCE_ONLY: "来源独有，未导入",
}
_PROBLEM_OUTCOMES = {
    ImportPreviewOutcome.SOURCE_CONFLICT,
    ImportPreviewOutcome.SCOPE_AMBIGUOUS,
    ImportPreviewOutcome.SOURCE_ONLY,
}


def _scope(row: ImportPreviewRow) -> str:
    if row.scope_kind is None:
        return "不适用"
    return "整个工程" if row.plugin_id is None else f"插件：{row.plugin_id}"


def row_details(row: ImportPreviewRow) -> str:
    if row.outcome is ImportPreviewOutcome.SOURCE_CONFLICT:
        explanation = "外部来源为同一原词提供多个译名，无法自动选择；此映射保持项目默认译名。"
    elif row.outcome is ImportPreviewOutcome.SCOPE_AMBIGUOUS:
        scopes = "、".join(
            "整个工程" if item == "project" else f"插件：{item.partition(':')[2]}" for item in row.base_scopes
        )
        explanation = (
            f"项目中同一原词存在多个范围（{scopes}）；外部来源未指定适用范围，无法自动分配。"
            "此映射保持项目默认译名，可在创建后管理译名方案并逐项调整。"
        )
    elif row.outcome is ImportPreviewOutcome.SOURCE_ONLY:
        explanation = "项目当前已发布的有效术语中没有此原词，因此不会写入译名方案。此行仅供审查。"
    elif row.outcome is ImportPreviewOutcome.ADOPTED:
        explanation = "此映射采用外部来源的译名。"
    else:
        explanation = "外部来源没有此原词，继续使用项目默认译名。"
    return (
        f"原词：{row.original}\n范围：{_scope(row)}\n"
        f"项目默认译名：{row.base_translation or '无'}\n"
        f"外部来源译名：{' / '.join(row.source_translations) or '无'}\n"
        f"方案译名：{row.translation or '未导入'}\n{explanation}"
    )


class ImportPreviewTableModel(QAbstractTableModel):
    headers = ("原文术语", "项目默认译名", "方案译名", "使用范围", "处理结果")

    def __init__(self, rows: tuple[ImportPreviewRow, ...], parent=None) -> None:
        super().__init__(parent)
        self.rows = rows

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.headers[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        row = self.rows[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return row
        if role == Qt.ItemDataRole.ToolTipRole:
            return row_details(row)
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                row.original,
                row.base_translation or "—",
                row.translation or "未导入",
                _scope(row),
                OUTCOME_LABELS[row.outcome],
            )[index.column()]
        return None


class ImportPreviewFilterModel(FilteredRowsModel):
    status_changed = pyqtSignal(bool, str)

    def __init__(self, model: ImportPreviewTableModel, parent=None) -> None:
        super().__init__(model, parent)
        self.search = AsyncRowFilter(parent or self)
        self.search.matches_changed.connect(self.set_matches)
        self.search.status_changed.connect(self.status_changed)
        self.search.set_rows(model.rows, _search_document)

    def set_filters(self, text: str, outcome: str, *, debounce=True) -> None:
        facets = ("", "problems") if outcome == "problems" else ("" if outcome == "all" else outcome, "")
        self.search.set_query(text, facets, debounce=debounce)

    def close(self):
        self.search.close()


def _search_document(row) -> SearchDocument:
    return SearchDocument(
        (row_details(row) + " " + OUTCOME_LABELS[row.outcome],),
        (row.outcome.value, "problems" if row.outcome in _PROBLEM_OUTCOMES else ""),
    )


__all__ = ["ImportPreviewFilterModel", "ImportPreviewTableModel", "OUTCOME_LABELS", "row_details"]
