# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Stable scope resolution for Workbench entry context-menu actions."""

from __future__ import annotations

from collections.abc import Iterable

from transbridge.application.io.identity import EntryKey
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui.project_labels import entry_label_key


def resolve_entry_action_scope(
    clicked_entry: TranslationEntry,
    entries: Iterable[TranslationEntry],
    selected_ids: Iterable[str | EntryKey],
) -> tuple[TranslationEntry, ...]:
    """Use the full selection only when the context-menu row belongs to it."""

    selected = set(selected_ids)

    def includes(entry):
        return entry.identity in selected or entry_label_key(entry) in selected

    if not clicked_entry.id or not includes(clicked_entry):
        return (clicked_entry,)
    return tuple(entry for entry in entries if entry.id and includes(entry))


__all__ = ["resolve_entry_action_scope"]
