"""Immutable search documents and cancellable matching, independent of Qt models."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import Event
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class SearchDocument:
    fields: tuple[str, ...]
    facets: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RowMatches:
    rows: range | tuple[int, ...]
    positions: Mapping[int, int] | None = None

    @classmethod
    def all(cls, count: int):
        return cls(range(count))

    def visible_row(self, source_row: int) -> int | None:
        if self.positions is None:
            return source_row if source_row in self.rows else None
        return self.positions.get(source_row)


def build_search_index(
    rows: tuple, document: Callable[[object], SearchDocument], cancelled: Event
) -> tuple[SearchDocument, ...] | None:
    indexed = []
    for row in rows:
        if cancelled.is_set():
            return None
        value = document(row)
        indexed.append(SearchDocument(tuple(field.casefold() for field in value.fields), value.facets))
    return tuple(indexed)


def match_search_index(
    index: tuple[SearchDocument, ...], text: str, facets: tuple[str, ...], cancelled: Event
) -> RowMatches | None:
    rows, positions = [], {}
    for source_row, document in enumerate(index):
        if cancelled.is_set():
            return None
        if any(
            value and (position >= len(document.facets) or value != document.facets[position])
            for position, value in enumerate(facets)
        ):
            continue
        if text and not any(text in field for field in document.fields):
            continue
        positions[source_row] = len(rows)
        rows.append(source_row)
    return RowMatches(tuple(rows), MappingProxyType(positions))
