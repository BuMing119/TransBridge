from __future__ import annotations

import time

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui.workbench.filters_presenter import FiltersPresenter, FilterState
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
