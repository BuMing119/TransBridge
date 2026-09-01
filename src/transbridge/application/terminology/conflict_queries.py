"""Structured conflict filters shared by GUI and repository query adapters."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .identity import canonical_digest
from .models import BuildResultRef, ConflictGroup, ConflictRisk

if TYPE_CHECKING:
    from .ports import PageRequest


@dataclass(frozen=True, slots=True)
class ConflictFilter:
    search: str = ""
    risk: ConflictRisk | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "search", self.search.strip().casefold())
        if self.risk is not None:
            object.__setattr__(self, "risk", ConflictRisk(self.risk))

    def matches(self, conflict: ConflictGroup) -> bool:
        if self.risk is not None and conflict.risk is not self.risk:
            return False
        values = (conflict.normalized_original, *(item.normalized_translation for item in conflict.variants))
        return not self.search or any(self.search in value.casefold() for value in values)

    def bind_request(self, request: PageRequest) -> PageRequest:
        """Bind cursors to actual filters even when callers use the default fingerprint."""

        if not self.search and self.risk is None:
            return request
        fingerprint = canonical_digest(
            (request.query_fingerprint, self.search, self.risk), namespace="terminology.conflict-query.v1"
        )
        return replace(request, query_fingerprint=fingerprint)


@dataclass(frozen=True, slots=True)
class ConflictQuery:
    build_ref: BuildResultRef
    filters: ConflictFilter = ConflictFilter()
