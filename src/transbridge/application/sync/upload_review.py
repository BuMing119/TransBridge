"""Snapshot-bound upload review choices, excluded from public plan projections."""

from dataclasses import dataclass

from .download_review import DownloadCandidate, DownloadReviewRow

UploadCandidate = DownloadCandidate
UploadReviewRow = DownloadReviewRow


@dataclass(frozen=True, slots=True)
class UploadDecision:
    row_id: str
    choice_token: str

    def __post_init__(self) -> None:
        if not self.row_id or not self.choice_token:
            raise ValueError("upload decision requires a row and a choice")


class UploadDecisionStaleError(ValueError):
    code = "STALE_UPLOAD_DECISION"
