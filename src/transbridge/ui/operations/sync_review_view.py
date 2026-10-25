"""Qt-free, user-facing projections for actionable synchronization review."""

from __future__ import annotations

from dataclasses import dataclass

KEEP_LOCAL = "keep_local"
KEEP_REMOTE = "keep_remote"


@dataclass(frozen=True, slots=True)
class SyncReviewCandidate:
    candidate_id: str
    label: str
    original: str
    translation: str
    context: str = ""
    selectable: bool = True
    disabled_reason: str = ""
    stage: int = 0
    source_count: int = 1


@dataclass(frozen=True, slots=True)
class SyncReviewRow:
    row_id: str
    key: str
    original: str
    translation: str
    context: str
    reason: str
    candidates: tuple[SyncReviewCandidate, ...] = ()
    decision: str = ""
    can_select_remote: bool = True
    selection_disabled_reason: str = ""
    preserves_local_source: bool = True
    stage: int = 0
