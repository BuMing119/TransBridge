"""Qt-free DTOs shared by legacy UI projection adapters."""

from __future__ import annotations

from dataclasses import dataclass

from transbridge.converter.translation_entry_collection import TranslationEntryCollection


@dataclass(slots=True)
class CollectionSlot:
    """One parsed collection and the source metadata required by UI adapters."""

    label: str
    collection: TranslationEntryCollection
    esp_path: str | None = None
    eet_path: str | None = None
    xt_path: str | None = None
    strings_path: str | None = None
    strings_lang: str = "chinese"
    sst_path: str | None = None
    migrate_count: int = 0
    plugin: object = None
    strings_lookup: object = None
    source_snapshot: object = None
    format_id: object = None
