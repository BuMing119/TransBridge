"""Read-only application use case for synchronization plans."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from transbridge.application.io.identity import SourceNamespace
from transbridge.application.ports.paratranz import CancellationPort

from .download_review import DownloadDecision
from .models import (
    ConflictPolicy,
    DeletionPolicy,
    LocalEntrySnapshot,
    RemoteEntrySnapshot,
    SyncOperation,
    SyncPlan,
)
from .planner import SyncPlanner
from .upload_review import UploadDecision


class RemoteSyncSnapshotPort(Protocol):
    def fetch(
        self,
        project_id: int,
        namespace: SourceNamespace,
        *,
        limit: int,
        cancellation: CancellationPort | None = None,
    ) -> tuple[RemoteEntrySnapshot, ...]: ...


@dataclass(frozen=True, slots=True)
class CreateSyncPlanRequest:
    project_id: int
    namespace: SourceNamespace
    local_entries: tuple[LocalEntrySnapshot, ...]
    operation: SyncOperation
    conflict_policy: ConflictPolicy = ConflictPolicy.ABORT
    deletion_policy: DeletionPolicy = DeletionPolicy.APPLY
    remote_limit: int = 100_000
    cancellation: CancellationPort | None = None
    download_decisions: tuple[DownloadDecision, ...] = ()
    upload_decisions: tuple[UploadDecision, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.project_id, bool) or not isinstance(self.project_id, int) or self.project_id < 1:
            raise ValueError("project_id must be a positive integer")
        if isinstance(self.remote_limit, bool) or not isinstance(self.remote_limit, int) or self.remote_limit < 1:
            raise ValueError("remote_limit must be a positive integer")
        object.__setattr__(self, "local_entries", tuple(self.local_entries))
        object.__setattr__(self, "download_decisions", tuple(self.download_decisions))
        object.__setattr__(self, "upload_decisions", tuple(self.upload_decisions))
        if any(entry.entry_key.namespace != self.namespace for entry in self.local_entries):
            raise ValueError("all local entries must belong to the requested source namespace")


class ParaTranzSyncPlanningUseCase:
    """Read snapshots and build a reviewable plan without executing it."""

    def __init__(
        self,
        remote_snapshots: RemoteSyncSnapshotPort,
        *,
        planner: SyncPlanner | None = None,
    ) -> None:
        self._remote_snapshots = remote_snapshots
        self._planner = planner or SyncPlanner()

    def create_plan(self, request: CreateSyncPlanRequest) -> SyncPlan:
        remote_entries = self._remote_snapshots.fetch(
            request.project_id,
            request.namespace,
            limit=request.remote_limit,
            cancellation=request.cancellation,
        )
        return self._planner.plan(
            request.local_entries,
            remote_entries,
            operation=request.operation,
            conflict_policy=request.conflict_policy,
            deletion_policy=request.deletion_policy,
            scope=_sync_scope(request.project_id, request.namespace),
            download_decisions=request.download_decisions,
            upload_decisions=request.upload_decisions,
        )


def _sync_scope(project_id: int, namespace: SourceNamespace) -> str:
    return f"paratranz:project:{project_id}:source:{namespace.value}"
