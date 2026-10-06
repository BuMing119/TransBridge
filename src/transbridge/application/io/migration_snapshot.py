"""Detached migration entries that share only known immutable payloads."""

from __future__ import annotations

from collections.abc import Iterable
from copy import copy, deepcopy

from transbridge.converter.translation_entry import TranslationEntry

from .identity import ExternalEntryRef, Provenance


def _immutable(value: object) -> bool:
    if type(value) in (str, int, float, bool, bytes, type(None)):
        return True
    if type(value) in (tuple, frozenset):
        return all(_immutable(item) for item in value)
    if type(value) in (ExternalEntryRef, Provenance):
        return _immutable(value.metadata)
    return False


def detached_entries(entries: Iterable[TranslationEntry]) -> tuple[TranslationEntry, ...]:
    """Copy mutable entry shells and nested metadata, not immutable identity trees.

    TranslationEntry itself remains mutable for legacy callers, so neither the
    live entry nor nested user metadata may be shared with a worker or draft.
    EntryKey/SourceNamespace/EntryRevision and scalar fields are immutable;
    external references and provenance are shareable only with immutable metadata.
    """
    memo: dict = {}
    result = []
    for entry in entries:
        detached = copy(entry)
        for name in ("metadata", "external_refs", "provenance"):
            value = getattr(entry, name)
            if not _immutable(value):
                object.__setattr__(detached, name, deepcopy(value, memo))
        result.append(detached)
    return tuple(result)
