# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Task-owned result selection and project-commit evidence contracts."""

from dataclasses import dataclass
from enum import StrEnum

from transbridge.application.io.identity import EntryKey


class ApplicationState(StrEnum):
    PREPARED = "prepared"
    STAGED_UNSAVED = "staged-unsaved"
    COMMITTED = "committed"
    ACKNOWLEDGEMENT_PENDING = "acknowledgement-pending"
    CONFLICTED = "conflicted"
    TARGET_UNAVAILABLE = "target-unavailable"
    DISCARDED = "discarded-or-superseded"


@dataclass(frozen=True, slots=True)
class ApplyCandidate:
    entry_key: EntryKey
    revision: int

    def __post_init__(self):
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("Candidate revision must be a positive integer")


@dataclass(frozen=True, slots=True)
class ApplyRequest:
    operation_id: str
    run_id: str
    project_id: str
    variant_id: str
    candidates: tuple[ApplyCandidate, ...]
    policy: str
    decision: str = "merge"

    def __post_init__(self):
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (self.operation_id, self.run_id, self.project_id, self.variant_id, self.policy, self.decision)
        ):
            raise ValueError("Task application requires operation, run, target, policy and decision")
        if len({item.entry_key for item in self.candidates}) != len(self.candidates) or not self.candidates:
            raise ValueError("Task application requires distinct candidates")


@dataclass(frozen=True, slots=True)
class TaskApplication:
    request: ApplyRequest
    state: ApplicationState
    receipt: dict | None = None
