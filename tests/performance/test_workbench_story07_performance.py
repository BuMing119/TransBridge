from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui.workbench.filters_presenter import FiltersPresenter, FilterState
from transbridge.ui.workbench.table_presenter import RenderSession
from transbridge.ui.workbench.translation_table import TranslationTable
from transbridge.ui.workbench.workflow_presenter import StatisticsSummary


def test_10k_summary_and_filter_projection_stays_linear_and_preserves_identity() -> None:
    entries = [
        TranslationEntry(
            str(index),
            f"key-{index}",
            f"Original {index}",
            "" if index % 2 else "译文",
            index % 3,
            "NPC_:FULL",
        )
        for index in range(10_000)
    ]
    presenter = FiltersPresenter()
    presenter.update(FilterState(search_key="key-99"))

    started = time.perf_counter()
    summary = StatisticsSummary.from_entries(entries)
    filtered = presenter.apply(entries, {})
    elapsed = time.perf_counter() - started

    assert summary.total == 10_000
    assert filtered
    assert all(item is entries[int(item.id)] for item in filtered)
    # A generous regression guard: the projection must remain one in-memory pass,
    # without a widget per row or quadratic identity lookup.
    assert elapsed < 0.5


def test_20k_project_replacement_stays_within_one_responsive_ui_turn() -> None:
    app = QApplication.instance() or QApplication([])
    entries = tuple(
        TranslationEntry(str(index), f"key-{index}", f"Original {index}", "Translation", 1, None)
        for index in range(20_000)
    )
    table = TranslationTable(on_progress=lambda *_: None, on_batch=lambda: None)
    table.resize(1_000, 600)
    table.show()
    try:
        table.start_render(RenderSession(1, 1, entries), {}, {})
        app.processEvents()
        durations = []
        for generation in range(2, 5):
            started = time.perf_counter()
            table.start_render(RenderSession(generation, generation, entries), {}, {})
            durations.append(time.perf_counter() - started)
            assert table.rowCount() == 20_000
            assert not table.has_pending_batch
            app.processEvents()
        # Replacing a project must not synchronously destroy 140,000 cell items.
        # The existing desktop responsiveness budget applies to each reset.
        assert max(durations) < 0.200
    finally:
        table.close_rendering()
        table.close()
        table.deleteLater()
        app.processEvents()
