"""Stable geometry helpers for ParaTranz data tables."""

from collections.abc import Mapping, Sequence

from PyQt6.QtWidgets import QHeaderView, QTableWidget


def configure_stable_table_columns(
    table: QTableWidget,
    *,
    fixed_widths: Mapping[int, int],
    stretch_columns: Sequence[int] = (),
) -> None:
    """Keep table geometry independent from the current row contents."""

    header = table.horizontalHeader()
    for column in range(table.columnCount()):
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
    for column, width in fixed_widths.items():
        header.resizeSection(column, width)
    for column in stretch_columns:
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
