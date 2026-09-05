"""Stable scope resolution for Workbench entry context-menu actions."""

from __future__ import annotations

from collections.abc import Iterable

from transbridge.converter.translation_entry import TranslationEntry


def resolve_entry_action_scope(
    clicked_entry: TranslationEntry,
    entries: Iterable[TranslationEntry],
    selected_ids: Iterable[str],
) -> tuple[TranslationEntry, ...]:
    """Use the full selection only when the context-menu row belongs to it."""

    selected = set(selected_ids)
    if not clicked_entry.id or clicked_entry.id not in selected:
        return (clicked_entry,)
    return tuple(entry for entry in entries if entry.id and entry.id in selected)


__all__ = ["resolve_entry_action_scope"]
