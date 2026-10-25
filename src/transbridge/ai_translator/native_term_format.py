"""Read the exact JSON records written by the canonical terminology serializer."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

_TEXT_FIELDS = frozenset({"term", "translation", "source", "context", "created_at", "pos", "note"})
_FIELDS = _TEXT_FIELDS | {"case_sensitive", "variants", "external_id", "metadata"}


def native_term_fields(record: Mapping[str, Any], *, source: str | None) -> dict[str, Any] | None:
    """Recognize complete native records without applying external import rules.

    Partial records, aliases and records with extra columns remain imports.
    Native values must have the serializer's types; malformed native data must
    not silently fall back to coercion and authorize a different stored value.
    """
    if record.keys() != _FIELDS:
        return None
    for name in _TEXT_FIELDS:
        if not isinstance(record[name], str):
            raise ValueError(f"原生术语记录的 {name} 必须为字符串")
    if not record["term"].strip() or not record["translation"].strip():
        return None  # Retain the import adapter's existing empty-row rejection.
    if not isinstance(record["case_sensitive"], bool):
        raise ValueError("原生术语记录的 case_sensitive 必须为布尔值")
    variants = record["variants"]
    if not isinstance(variants, list) or not all(isinstance(value, str) for value in variants):
        raise ValueError("原生术语记录的 variants 必须为字符串数组")
    identifier = record["external_id"]
    if identifier is not None and type(identifier) not in (int, str):
        raise ValueError("原生术语记录的 external_id 必须为整数、字符串或 null")
    metadata = record["metadata"]
    if not isinstance(metadata, dict) or not all(isinstance(key, str) for key in metadata):
        raise ValueError("原生术语记录的 metadata 必须为字符串键对象")
    result = deepcopy(dict(record))
    if source is not None:
        result["source"] = source
    return result
