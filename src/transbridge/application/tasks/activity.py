"""Immutable task activity contracts shared by presentation entrypoints.

This module deliberately derives display actions from authoritative snapshots
and explicitly injected evidence.  It never advances a task state and it does
not infer checkpoint, retry, log, or artifact support from a job type name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import JobSnapshot, JobState, OwnerRef


@dataclass(frozen=True, slots=True)
class TaskOwnerScope:
    """Permission-free owner identity safe to expose to a view."""

    owner_id: str
    entrypoint: str
    project_id: str | None = None
    variant_id: str | None = None
    session_id: str | None = None

    @classmethod
    def from_owner(cls, owner: OwnerRef) -> TaskOwnerScope:
        return cls(
            owner_id=owner.owner_id,
            entrypoint=owner.entrypoint,
            project_id=owner.project_id,
            variant_id=owner.variant_id,
            session_id=owner.session_id,
        )


@dataclass(frozen=True, slots=True)
class TaskDisplayContext:
    title: str
    entrypoint: str
    project_id: str | None = None
    variant_id: str | None = None
    session_id: str | None = None

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("task display title must not be empty")
        if not self.entrypoint.strip():
            raise ValueError("task display entrypoint must not be empty")


@dataclass(frozen=True, slots=True)
class TaskDiagnosticRef:
    """A safe reference to a diagnostic, not the possibly sensitive message."""

    code: str
    sequence: int

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValueError("diagnostic code must not be empty")
        if self.sequence < 0:
            raise ValueError("diagnostic sequence must not be negative")


@dataclass(frozen=True, slots=True)
class TaskArtifactRef:
    """Opaque artifact identity; paths and payloads stay behind artifact ports."""

    artifact_id: str
    kind: str
    label: str = ""

    def __post_init__(self) -> None:
        if not self.artifact_id.strip():
            raise ValueError("artifact_id must not be empty")
        if not self.kind.strip():
            raise ValueError("artifact kind must not be empty")


@dataclass(frozen=True, slots=True)
class TaskNavigationIntent:
    """Shell-owned navigation request with no concrete window reference."""

    target: str
    parameters: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.target.strip():
            raise ValueError("navigation target must not be empty")
        keys = tuple(key for key, _ in self.parameters)
        if any(not key.strip() for key in keys):
            raise ValueError("navigation parameter keys must not be empty")
        if len(set(keys)) != len(keys):
            raise ValueError("navigation parameter keys must be unique")


class TaskResultNavigator(Protocol):
    """Maps an activity to a shell intent without owning any view/window."""

    def resolve(self, snapshot: JobSnapshot, actor: OwnerRef) -> TaskNavigationIntent | None: ...


@dataclass(frozen=True, slots=True)
class TaskActionAvailability:
    pause: bool = False
    resume: bool = False
    cancel: bool = False
    stop: bool = False
    recover: bool = False
    retry: bool = False
    open_result: bool = False
    open_log: bool = False


@dataclass(frozen=True, slots=True)
class TaskActivityEvidence:
    """Evidence supplied by checkpoint/retry/artifact/log ports.

    All values default to unsupported.  Callers must opt in only after the
    corresponding application port proves the capability for this run and the
    current context.
    """

    recoverable: bool = False
    recoverability_reason: str = "checkpoint_not_available"
    retryable: bool = False
    result_available: bool = False
    log_available: bool = False
    artifact_refs: tuple[TaskArtifactRef, ...] = ()

    def __post_init__(self) -> None:
        if not self.recoverability_reason.strip():
            raise ValueError("recoverability_reason must not be empty")


class TaskActivityEvidencePort(Protocol):
    def for_snapshot(self, snapshot: JobSnapshot, actor: OwnerRef) -> TaskActivityEvidence: ...


class UnsupportedTaskActivityEvidence:
    """Safe default used until a feature registers real capability ports."""

    def for_snapshot(self, snapshot: JobSnapshot, actor: OwnerRef) -> TaskActivityEvidence:
        del snapshot, actor
        return TaskActivityEvidence()


@dataclass(frozen=True, slots=True)
class TaskActivityViewState:
    run_id: str
    job_id: str
    owner: TaskOwnerScope
    job_type: str
    display_context: TaskDisplayContext
    state: JobState
    revision: int
    sequence: int
    progress: tuple[tuple[str, object], ...]
    available_actions: TaskActionAvailability
    diagnostic_refs: tuple[TaskDiagnosticRef, ...] = ()
    artifact_refs: tuple[TaskArtifactRef, ...] = ()
    recoverability_reason: str = "checkpoint_not_available"

    @property
    def is_terminal(self) -> bool:
        return self.state in {JobState.CANCELLED, JobState.COMPLETED, JobState.FAILED}


def activity_from_snapshot(
    snapshot: JobSnapshot,
    *,
    evidence: TaskActivityEvidence | None = None,
    diagnostic_refs: tuple[TaskDiagnosticRef, ...] = (),
) -> TaskActivityViewState:
    """Build a permission-free, immutable activity state from one snapshot."""

    supplied = evidence or TaskActivityEvidence()
    capabilities = snapshot.specification.capabilities
    state = snapshot.state
    controllable = state in {JobState.QUEUED, JobState.RUNNING, JobState.PAUSED}
    actions = TaskActionAvailability(
        pause=capabilities.supports_pause and state is JobState.RUNNING,
        resume=capabilities.supports_resume and state is JobState.PAUSED,
        cancel=capabilities.supports_cancel and controllable,
        stop=capabilities.supports_cancel and controllable,
        recover=supplied.recoverable,
        retry=supplied.retryable,
        open_result=supplied.result_available,
        open_log=supplied.log_available,
    )
    title = snapshot.specification.display_name.strip() or snapshot.specification.job_type
    return TaskActivityViewState(
        run_id=snapshot.ref.run_id or snapshot.ref.job_id,
        job_id=snapshot.ref.job_id,
        owner=TaskOwnerScope.from_owner(snapshot.owner),
        job_type=snapshot.specification.job_type,
        display_context=TaskDisplayContext(
            title=title,
            entrypoint=snapshot.owner.entrypoint,
            project_id=snapshot.owner.project_id,
            variant_id=snapshot.owner.variant_id,
            session_id=snapshot.owner.session_id,
        ),
        state=state,
        revision=snapshot.revision,
        sequence=snapshot.sequence,
        progress=snapshot.progress,
        available_actions=actions,
        diagnostic_refs=diagnostic_refs,
        artifact_refs=supplied.artifact_refs,
        recoverability_reason=supplied.recoverability_reason,
    )
