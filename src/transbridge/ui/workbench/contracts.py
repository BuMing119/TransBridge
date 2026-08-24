"""Public, narrow ports exposed by Workbench facades."""

from __future__ import annotations

from typing import Protocol

from transbridge.converter.translation_entry import TranslationEntry


class WorkbenchSelectionPort(Protocol):
    def selected_entry_ids(self) -> tuple[str, ...]: ...

    def filtered_entries(self) -> tuple[TranslationEntry, ...]: ...

    def locate_entry(self, entry_id: str) -> None: ...

    def selected_row_entry_ids(self) -> tuple[str, ...]: ...


class WorkbenchIntentPort(Protocol):
    """Presentation surface that emits catalog-compatible intent IDs."""

    intent_requested: object
