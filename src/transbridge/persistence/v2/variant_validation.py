"""Reuse proven Variant rows while retaining document and cross-row checks."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
from typing import Any

from .ids import EntityKind, EntityRef

_PREIMAGE: ContextVar[tuple[EntityRef, bytes] | None] = ContextVar("variant_validation_preimage", default=None)


@contextmanager
def reuse_variant_preimage(ref: EntityRef, raw: bytes | None) -> Iterator[None]:
    """Bind only operation-owned bytes that already passed full validation."""
    token = _PREIMAGE.set(None if raw is None else (ref, raw))
    try:
        yield
    finally:
        _PREIMAGE.reset(token)


@dataclass(frozen=True, slots=True)
class VariantValidationView:
    document: dict[str, Any]
    entry_indexes: tuple[int, ...] | None = None

    def original_path(self, path: Sequence[object]) -> list[object]:
        result = list(path)
        if (
            self.entry_indexes is not None
            and len(result) >= 3
            and result[:2] == ["data", "entries"]
            and isinstance(result[2], int)
        ):
            result[2] = self.entry_indexes[result[2]]
        return result


def variant_validation_view(document: dict[str, Any], ref: EntityRef) -> VariantValidationView:
    """Omit only independently validated, type-identical rows from schema work.

    The caller must run global semantics against the original document. The
    Variant schema has no array-wide constraint on entries or key-wide constraint
    on translations/labels; their container types and every other field remain
    in the schema view. Reordering entries therefore only changes error mapping.
    """
    bound = _PREIMAGE.get()
    data = document.get("data")
    if ref.kind is not EntityKind.VARIANT or bound is None or bound[0] != ref or not isinstance(data, dict):
        return VariantValidationView(document)
    previous = json.loads(bound[1])["data"]
    filtered = dict(data)
    indexes = None
    entries = data.get("entries")
    previous_entries = previous.get("entries")
    if isinstance(entries, list) and isinstance(previous_entries, list):
        proven_rows = {encoded for row in previous_entries if (encoded := _canonical(row)) is not None}
        changed = [(index, row) for index, row in enumerate(entries) if _canonical(row) not in proven_rows]
        indexes = tuple(index for index, _row in changed)
        filtered["entries"] = [row for _index, row in changed]
    for field in ("translations", "labels"):
        values = data.get(field)
        previous_values = previous.get(field)
        if isinstance(values, dict) and isinstance(previous_values, dict):
            filtered[field] = {
                key: value
                for key, value in values.items()
                if key not in previous_values or not _identical(value, previous_values[key])
            }
    return VariantValidationView(dict(document, data=filtered), indexes)


def _canonical(value: object) -> bytes | None:
    # JSON spelling distinguishes bool/int and int/float, unlike Python equality.
    # Non-JSON values never qualify for reuse and retain normal schema diagnostics.
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    except (TypeError, ValueError):
        return None


def _identical(value: object, previous: object) -> bool:
    encoded = _canonical(value)
    return encoded is not None and encoded == _canonical(previous)
