"""In-memory review contracts for snapshot-bound download decisions.

Review text is deliberately excluded from public plan projections and hashes.
The resulting action, summaries and external reference bind the executed choice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import LocalEntrySnapshot, RemoteEntrySnapshot


@dataclass(frozen=True, slots=True)
class DownloadDecision:
    row_id: str
    choice_token: str

    def __post_init__(self) -> None:
        if not self.row_id or not self.choice_token:
            raise ValueError("download decision requires a row and a choice")


@dataclass(frozen=True, slots=True)
class DownloadCandidate:
    token: str
    entry: RemoteEntrySnapshot
    selectable: bool = True
    reason: str = ""
    equivalent_entries: tuple[RemoteEntrySnapshot, ...] = ()

    @property
    def source_count(self) -> int:
        return len(self.equivalent_entries) or 1


@dataclass(frozen=True, slots=True)
class DownloadReviewRow:
    row_id: str
    local: LocalEntrySnapshot
    candidates: tuple[DownloadCandidate, ...]
    reason: str
    selected_choice: str = ""
    local_occurrences: int = 1
    allow_remote_choice: bool = True
    resolved: bool = False


class DownloadDecisionStaleError(ValueError):
    code = "STALE_DOWNLOAD_DECISION"
