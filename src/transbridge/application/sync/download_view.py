# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Immutable presentation values for a multi-source download review."""

from __future__ import annotations

from dataclasses import dataclass

from .review_view import SyncReviewRow


class RetryableDownloadSaveError(RuntimeError):
    """A downloaded candidate is retained and only local publication needs retrying."""


@dataclass(frozen=True, slots=True)
class DownloadSourceOption:
    source_id: str
    label: str
    count: int
    selected: bool = True


@dataclass(frozen=True, slots=True)
class DownloadPreview:
    updates: int
    unchanged: int
    deferred: int
    details: tuple[str, ...] = ()
    review_rows: tuple[SyncReviewRow, ...] = ()
    can_apply: bool = True
    status: str = ""
    can_save: bool = False
    retryable: bool = False
    source_ids: tuple[str, ...] = ()
    policy: str = ""


@dataclass(frozen=True, slots=True)
class DownloadReceipt:
    updated: int
    details: tuple[str, ...] = ()
    requires_review: bool = False
    preview: DownloadPreview | None = None
    retryable: bool = False
