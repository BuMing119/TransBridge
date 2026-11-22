# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Qt-free DTOs shared by legacy UI projection adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PureWindowsPath

from transbridge.application.io.dialogue_reading_view import ReadingEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection


def projected_variant_name(variants, active_id) -> str | None:
    return next((str(item["name"]) for item in variants if str(item["id"]) == str(active_id)), None)


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
    source_id: str | None = None
    source_namespace: str | None = None
    reading_source_entries: tuple[ReadingEntry, ...] | None = None
    reference_path: str = ""

    def content_name(self, key: str | None) -> str:
        """Display name used by the workbench's active-content selector."""
        return self.label or (PureWindowsPath(key).name if key else "未命名内容")

    def content_label(self, key: str) -> str:
        name = self.content_name(key)
        if self.plugin is not None or self.esp_path:
            return f"插件 · {name}"
        if self.strings_path:
            return f"本地化字符串 · {name}"
        return f"翻译内容 · {name}"
