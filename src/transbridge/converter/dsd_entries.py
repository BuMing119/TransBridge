"""Batch DSD parsing preserves records with the same public locator."""

from collections.abc import Iterable, Mapping
import logging
from typing import Any

from transbridge.converter.plugin_entry_conflicts import resolve_plugin_entry_conflicts
from transbridge.converter.translation_entry import TranslationEntry


def parse_dsd_entries(records: Iterable[Mapping[str, Any]]) -> list[TranslationEntry]:
    """Disambiguate exact originals before constructing a keyed collection."""
    entries = [TranslationEntry.from_dsd_dict(record) for record in records]
    valid, diagnostics = resolve_plugin_entry_conflicts(entries)
    if diagnostics:
        logging.getLogger(__name__).warning(
            "SOURCE_LOCATOR_CONFLICT: DSD 中 %d 条定位与原文均相同的条目已跳过，保留 %d 条。",
            len(diagnostics),
            len(valid),
        )
    return valid
