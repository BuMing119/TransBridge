# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Qt-free filtering state and projection for the Workbench preview."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from transbridge.application.workbench_filters import (
    FilterState as FilterState,
    entry_category as entry_category,
    filter_entries,
)
from transbridge.converter.context_categories import ALL_DISPLAY_CATEGORIES
from transbridge.converter.translation_entry import TranslationEntry

ALL_CATEGORIES = ALL_DISPLAY_CATEGORIES


class FiltersPresenter:
    """Apply a filter intent without owning or copying the source collection."""

    def __init__(self) -> None:
        self._revision = 0
        self._state = FilterState()

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def state(self) -> FilterState:
        return self._state

    def update(self, state: FilterState) -> None:
        if state != self._state:
            self._state = state
            self._revision += 1

    def apply(
        self,
        entries: Iterable[TranslationEntry],
        entry_labels: Mapping[str, set[str]],
    ) -> list[TranslationEntry]:
        return filter_entries(entries, self._state, entry_labels)
