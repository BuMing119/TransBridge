"""Immutable task identities, state and authorization scope."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from transbridge.application.contracts import JobRef


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


TERMINAL_STATES = frozenset({JobState.CANCELLED, JobState.COMPLETED, JobState.FAILED})


@dataclass(frozen=True, slots=True)
class OwnerRef:
    """Application owner scope used for every task query and control."""

    owner_id: str
    entrypoint: str
    project_id: str | None = None
    variant_id: str | None = None
    session_id: str | None = None
    permissions: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.owner_id.strip():
            raise ValueError("owner_id must not be empty")
        if not self.entrypoint.strip():
            raise ValueError("entrypoint must not be empty")
        if any(not permission.strip() for permission in self.permissions):
            raise ValueError("permissions must not contain empty values")

    def same_scope(self, other: OwnerRef) -> bool:
        return (
            self.owner_id,
            self.entrypoint,
            self.project_id,
            self.variant_id,
            self.session_id,
        ) == (
            other.owner_id,
            other.entrypoint,
            other.project_id,
            other.variant_id,
            other.session_id,
        )


@dataclass(frozen=True, slots=True)
class JobCapabilities:
    supports_pause: bool = False
    supports_resume: bool = False
    supports_cancel: bool = True
    supports_checkpoint: bool = False

    def __post_init__(self) -> None:
        if self.supports_resume and not self.supports_pause:
            raise ValueError("resume capability requires pause capability")


@dataclass(frozen=True, slots=True)
class JobSpec:
    job_type: str
    input_ref: str
    input_fingerprint: str
    display_name: str = ""
    config_digest: str | None = None
    capabilities: JobCapabilities = field(default_factory=JobCapabilities)
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.job_type.strip():
            raise ValueError("job_type must not be empty")
        if not self.input_ref.strip():
            raise ValueError("input_ref must not be empty")
        if not self.input_fingerprint.strip():
            raise ValueError("input_fingerprint must not be empty")
        if len({key for key, _ in self.metadata}) != len(self.metadata):
            raise ValueError("metadata keys must be unique")
        if any(not key.strip() for key, _ in self.metadata):
            raise ValueError("metadata keys must not be empty")


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    ref: JobRef
    owner: OwnerRef
    specification: JobSpec
    state: JobState
    revision: int
    sequence: int
    created_at: datetime
    updated_at: datetime
    progress: tuple[tuple[str, object], ...] = ()

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES


class TaskAccessError(PermissionError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TransitionError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        ref: JobRef,
        current: JobState,
        target: JobState,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.ref = ref
        self.current = current
        self.target = target
