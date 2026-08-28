"""Typed failures for the project terminology domain."""

from __future__ import annotations


class TerminologyError(RuntimeError):
    """Base error carrying a stable machine-readable code."""

    code = "TERMINOLOGY_ERROR"


class RevisionConflictError(TerminologyError):
    code = "REVISION_CONFLICT"

    def __init__(self, expected_revision: int, actual_revision: int | None) -> None:
        super().__init__(f"expected revision {expected_revision}, found {actual_revision}")
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision


class DigestCollisionError(TerminologyError):
    code = "DIGEST_COLLISION"

    def __init__(self, digest: str) -> None:
        super().__init__(f"digest {digest!r} identifies different canonical payloads")
        self.digest = digest


class StaleBuildError(TerminologyError):
    code = "STALE_BUILD"


class CursorStaleError(TerminologyError):
    code = "CURSOR_STALE"


class ActiveDraftError(TerminologyError):
    code = "ACTIVE_DRAFT_EXISTS"


class RepositoryConflictError(TerminologyError):
    code = "REPOSITORY_CONFLICT"


class TerminologyNotFoundError(TerminologyError):
    code = "TERMINOLOGY_NOT_FOUND"


__all__ = [
    "ActiveDraftError",
    "CursorStaleError",
    "DigestCollisionError",
    "RepositoryConflictError",
    "RevisionConflictError",
    "StaleBuildError",
    "TerminologyError",
    "TerminologyNotFoundError",
]
