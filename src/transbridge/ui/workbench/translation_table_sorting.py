"""Header sort intent and source-index ordering, confined to the table view."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTableView

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui.project_labels import entry_label_key

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


def _sort_value(entry, entry_labels, column):
    if column == COL_MARK:
        return len(entry_labels.get(entry_label_key(entry), ()))
    if column == COL_KEY:
        return (entry.key or "").casefold()
    if column == COL_ORIGINAL:
        return (entry.original or "").casefold()
    if column == COL_TRANSLATION:
        return (entry.translation or "").casefold()
    if column == COL_CONTEXT:
        return entry_category(entry), entry.stage
    raise ValueError(f"Unsupported translation table sort column: {column}")


def ordered_source_rows(
    entries: Sequence[TranslationEntry],
    entry_labels: Mapping[str, set[str]],
    column: int | None,
    descending: bool = False,
    *,
    default_ranks: Mapping[object, int] | None = None,
    default_rows: tuple[int, ...] | None = None,
) -> tuple[int, ...]:
    """Sort lightweight indices once, using full values and stable source-order ties.

    No Qt-item comparisons, entry copies, or mutations of the source collection.
    Python's key sort extracts one value per entry, rather than per comparison.
    """
    rows = default_rows if default_rows is not None else tuple(range(len(entries)))
    if default_rows is None and default_ranks:
        fallback = len(default_ranks)
        rows = tuple(sorted(rows, key=lambda row: default_ranks.get(entries[row].identity, fallback + row)))
    if column is None:
        return tuple(rows)
    if column == COL_INDEX:
        return tuple(reversed(rows) if descending else rows)
    if column not in _SORT_HINTS:
        raise ValueError(f"Unsupported translation table sort column: {column}")
    values = [_sort_value(entry, entry_labels, column) for entry in entries]
    return tuple(sorted(rows, key=values.__getitem__, reverse=descending))


class TranslationTableSorting:
    """Own the three-state header interaction without enabling Qt item sorting."""

    def __init__(self, table: QTableView, on_changed: Callable[[], None]) -> None:
        self._column: int | None = None
        self._descending = False
        self._default_ranks: Mapping[object, int] = {}
        self._default_keys = ()
        self._source_keys = None
        self._default_rows = None
        self._prepared_source_keys = None
        self._prepared_source_rows = None
        self._manual_signature = self._manual_rows = None
        self._header = table.horizontalHeader()
        self._on_changed = on_changed
        self._header.setSectionsClickable(True)
        self._header.setSortIndicatorShown(False)
        self._header.setAccessibleName("词条排序表头")
        self._header.setAccessibleDescription("点击同一列依次切换升序、降序、默认顺序；仅改变显示。")
        for column, hint in _SORT_HINTS.items():
            table.model().setHeaderData(
                column,
                Qt.Orientation.Horizontal,
                f"{hint}。点击切换：升序 → 降序 → 默认。仅改变显示顺序。",
                Qt.ItemDataRole.ToolTipRole,
            )
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
        source_keys = tuple(entry.identity for entry in entries)
        if source_keys != self._source_keys:
            self._source_keys = source_keys
            self._default_rows = None
            self._manual_signature = self._manual_rows = None
        if self._default_rows is None:
            self._default_rows = (
                self._prepared_source_rows
                if source_keys == self._prepared_source_keys
                else ordered_source_rows(entries, {}, None, default_ranks=self._default_ranks)
            )
        if self._column in (None, COL_INDEX):
            return ordered_source_rows(
                entries, entry_labels, self._column, self._descending, default_rows=self._default_rows
            )
        values = tuple(_sort_value(entry, entry_labels, self._column) for entry in entries)
        signature = self._column, self._descending, values
        if signature != self._manual_signature:
            self._manual_signature = signature
            self._manual_rows = tuple(sorted(self._default_rows, key=values.__getitem__, reverse=self._descending))
        return self._manual_rows

    def ordering_affected(self, changes, entry_labels) -> bool:
        """Only the active column can reorder same-identity replacement entries."""
        return self._column not in (None, COL_INDEX) and any(
            _sort_value(before, entry_labels, self._column) != _sort_value(after, entry_labels, self._column)
            for before, after in changes
        )

    def set_default_order(self, keys, *, ranks=None, source_keys=None, source_rows=None) -> None:
        """Install complete-source ranks and optional immutable worker indices.

        Prepared mappings and source tuples must remain immutable after install.
        Filters only project this identity map; source values stay in the session.
        """
        keys = tuple(keys)
        if (
            keys is self._default_keys
            and (ranks is None or ranks is self._default_ranks)
            and source_keys is self._prepared_source_keys
            and source_rows is self._prepared_source_rows
        ):
            return
        self._default_keys = keys
        self._default_ranks = ranks if ranks is not None else {key: rank for rank, key in enumerate(keys)}
        self._prepared_source_keys, self._prepared_source_rows = source_keys, source_rows
        self._default_rows = None
        self._manual_signature = self._manual_rows = None

    @property
    def source_keys(self):
        return self._source_keys

    def clear(self) -> None:
        """Release source identities as well as ranks when the view closes."""
        self.set_default_order(())
        self._source_keys, self._default_rows = None, None
        self._manual_signature = self._manual_rows = None

    @property
    def default_ranks(self):
        return self._default_ranks
