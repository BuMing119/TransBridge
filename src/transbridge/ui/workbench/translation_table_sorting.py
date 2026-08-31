"""Header sort intent and source-index ordering, confined to the table view."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTableWidget

from transbridge.converter.translation_entry import TranslationEntry

from .filters_presenter import entry_category
from .translation_table_columns import COL_CONTEXT, COL_INDEX, COL_KEY, COL_MARK, COL_ORIGINAL, COL_TRANSLATION

_SORT_HINTS = {
    COL_INDEX: "按默认序号排序",
    COL_MARK: "按标签数量排序",
    COL_KEY: "按完整 Key 排序（忽略大小写）",
    COL_ORIGINAL: "按完整原文排序（忽略大小写）",
    COL_TRANSLATION: "按完整译文排序（忽略大小写，空译文升序时在前）",
    COL_CONTEXT: "先按类型名称，再按翻译状态顺序排序",
}


def ordered_source_rows(
    entries: Sequence[TranslationEntry],
    entry_labels: Mapping[str, set[str]],
    column: int | None,
    descending: bool = False,
) -> tuple[int, ...]:
    """Sort lightweight indices once, using full values and stable source-order ties.

    No Qt-item comparisons, entry copies, or mutations of the source collection.
    Python's key sort extracts one value per entry, rather than per comparison.
    """
    rows = range(len(entries))
    if column is None:
        return tuple(rows)
    if column == COL_INDEX:
        return tuple(reversed(rows) if descending else rows)
    if column == COL_MARK:
        values = [len(entry_labels.get(entry.id, ())) for entry in entries]
    elif column == COL_KEY:
        values = [(entry.key or "").casefold() for entry in entries]
    elif column == COL_ORIGINAL:
        values = [(entry.original or "").casefold() for entry in entries]
    elif column == COL_TRANSLATION:
        values = [(entry.translation or "").casefold() for entry in entries]
    elif column == COL_CONTEXT:
        values = [(entry_category(entry), entry.stage) for entry in entries]
    else:
        raise ValueError(f"Unsupported translation table sort column: {column}")
    return tuple(sorted(rows, key=values.__getitem__, reverse=descending))


class TranslationTableSorting:
    """Own the three-state header interaction without enabling Qt item sorting."""

    def __init__(self, table: QTableWidget, on_changed: Callable[[], None]) -> None:
        self._column: int | None = None
        self._descending = False
        self._header = table.horizontalHeader()
        self._on_changed = on_changed
        self._header.setSectionsClickable(True)
        self._header.setSortIndicatorShown(False)
        self._header.setAccessibleName("词条排序表头")
        self._header.setAccessibleDescription("点击同一列依次切换升序、降序、默认顺序；仅改变显示。")
        for column, hint in _SORT_HINTS.items():
            table.horizontalHeaderItem(column).setToolTip(f"{hint}。点击切换：升序 → 降序 → 默认。仅改变显示顺序。")
        self._header.sectionClicked.connect(self._on_clicked)

    def _on_clicked(self, column: int) -> None:
        if column not in _SORT_HINTS:
            self._show_indicator()
            return
        if column != self._column:
            self._column, self._descending = column, False
        elif not self._descending:
            self._descending = True
        else:
            self._column, self._descending = None, False
        self._show_indicator()
        self._on_changed()

    def _show_indicator(self) -> None:
        # QHeaderView may move its arrow even when Qt's item sorting is disabled.
        if self._column is not None:
            order = Qt.SortOrder.DescendingOrder if self._descending else Qt.SortOrder.AscendingOrder
            self._header.setSortIndicator(self._column, order)
        self._header.setSortIndicatorShown(self._column is not None)

    def order(self, entries: Sequence[TranslationEntry], entry_labels: Mapping[str, set[str]]) -> tuple[int, ...]:
        return ordered_source_rows(entries, entry_labels, self._column, self._descending)
