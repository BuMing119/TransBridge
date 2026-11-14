# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
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
    include_equivalent: bool = True
    review_state: str = "all"
    start_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "search", self.search.strip().casefold())
        if self.risk is not None:
            object.__setattr__(self, "risk", ConflictRisk(self.risk))
        if self.review_state not in {"all", "pending", "resolved", "equivalent"}:
            raise ValueError("unknown conflict review state")
        if self.start_at is not None and not self.start_at.strip():
            raise ValueError("conflict starting identity must not be empty")

    def matches(self, conflict: ConflictGroup) -> bool:
        if self.start_at is not None and conflict.conflict_group_id < self.start_at:
            return False
        if not self.include_equivalent and conflict.status == "equivalent":
            return False
        if self.review_state == "pending" and conflict.status != "unresolved":
            return False
        if self.review_state == "equivalent" and conflict.status != "equivalent":
            return False
        if self.review_state == "resolved" and conflict.status not in {"unified", "plugin_exception", "ignored"}:
            return False
        if self.risk is not None and conflict.risk is not self.risk:
            return False
        values = (conflict.normalized_original, *(item.normalized_translation for item in conflict.variants))
        return not self.search or any(self.search in value.casefold() for value in values)

    def bind_request(self, request: PageRequest) -> PageRequest:
        """Bind cursors to actual filters even when callers use the default fingerprint."""

        if (
            not self.search
            and self.risk is None
            and self.include_equivalent
            and self.review_state == "all"
            and self.start_at is None
        ):
            return request
        fingerprint = canonical_digest(
            (
                request.query_fingerprint,
                self.search,
                self.risk,
                self.include_equivalent,
                self.review_state,
                self.start_at,
            ),
            namespace="terminology.conflict-query.v1",
        )
        return replace(request, query_fingerprint=fingerprint)


@dataclass(frozen=True, slots=True)
class ConflictQuery:
    build_ref: BuildResultRef
    filters: ConflictFilter = ConflictFilter()
