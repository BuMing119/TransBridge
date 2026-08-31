"""Application use case for dry-run plans and one-use authorization."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Protocol

from transbridge.application.io.identity import SourceNamespace
from transbridge.application.ports.paratranz import CancellationPort
from transbridge.application.security.hitl import (
    ConfirmationAuthority,
    ConfirmationToken,
)

from .models import (
    ConflictPolicy,
    DeletionPolicy,
    LocalEntrySnapshot,
    RemoteEntrySnapshot,
    SyncOperation,
    SyncPlan,
)
from .planner import SyncPlanner


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

    def __post_init__(self) -> None:
        if isinstance(self.project_id, bool) or not isinstance(self.project_id, int) or self.project_id < 1:
            raise ValueError("project_id must be a positive integer")
        if isinstance(self.remote_limit, bool) or not isinstance(self.remote_limit, int) or self.remote_limit < 1:
            raise ValueError("remote_limit must be a positive integer")
        object.__setattr__(self, "local_entries", tuple(self.local_entries))
        if any(entry.entry_key.namespace != self.namespace for entry in self.local_entries):
            raise ValueError("all local entries must belong to the requested source namespace")


@dataclass(frozen=True, slots=True)
class AuthorizeSyncPlanRequest:
    plan: SyncPlan
    owner_id: str
    project_id: int
    namespace: SourceNamespace
    current_local_entries: tuple[LocalEntrySnapshot, ...]
    confirmation_token: ConfirmationToken | None = None
    remote_limit: int = 100_000
    cancellation: CancellationPort | None = None

    def __post_init__(self) -> None:
        if not self.owner_id.strip():
            raise ValueError("sync confirmation owner_id must not be empty")
        if isinstance(self.project_id, bool) or not isinstance(self.project_id, int) or self.project_id < 1:
            raise ValueError("project_id must be a positive integer")
        object.__setattr__(self, "current_local_entries", tuple(self.current_local_entries))
        if any(entry.entry_key.namespace != self.namespace for entry in self.current_local_entries):
            raise ValueError("all current local entries must belong to the requested source namespace")


@dataclass(frozen=True, slots=True)
class AuthorizedSyncPlan:
    plan: SyncPlan
    owner_id: str
    confirmation_code: str


class SyncPlanStaleError(RuntimeError):
    code = "STALE_PLAN"


class SyncPlanAuthorizationError(RuntimeError):
    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        super().__init__(reason)


class ParaTranzSyncPlanningUseCase:
    """Read snapshots, build plans, then authorize fresh immutable plans."""

    def __init__(
        self,
        remote_snapshots: RemoteSyncSnapshotPort,
        *,
        planner: SyncPlanner | None = None,
        confirmations: ConfirmationAuthority | None = None,
    ) -> None:
        self._remote_snapshots = remote_snapshots
        self._planner = planner or SyncPlanner()
        self._confirmations = confirmations or ConfirmationAuthority()

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
        )

    def issue_confirmation(self, plan: SyncPlan, *, owner_id: str) -> ConfirmationToken:
        if not owner_id.strip():
            raise ValueError("sync confirmation owner_id must not be empty")
        _verify_plan(plan)
        return self._confirmations.issue(
            owner_id=owner_id,
            request_hash=_confirmation_request_hash(plan),
        )

    def authorize(self, request: AuthorizeSyncPlanRequest) -> AuthorizedSyncPlan:
        _verify_plan(request.plan)
        if request.plan.scope != _sync_scope(request.project_id, request.namespace):
            raise SyncPlanAuthorizationError(
                "PLAN_SCOPE_CHANGED",
                "sync plan does not belong to the requested project and source scope",
            )
        current_remote = self._remote_snapshots.fetch(
            request.project_id,
            request.namespace,
            limit=request.remote_limit,
            cancellation=request.cancellation,
        )
        current_hashes = self._planner.snapshot_hashes(
            request.current_local_entries,
            current_remote,
        )
        expected_hashes = (
            request.plan.local_snapshot_hash,
            request.plan.remote_snapshot_hash,
        )
        if current_hashes != expected_hashes:
            raise SyncPlanStaleError("local or remote sync snapshot changed after planning")
        if not request.plan.requires_confirmation:
            return AuthorizedSyncPlan(request.plan, request.owner_id, "NOT_REQUIRED")
        decision = self._confirmations.consume(
            request.confirmation_token,
            owner_id=request.owner_id,
            request_hash=_confirmation_request_hash(request.plan),
        )
        if not decision.allowed:
            raise SyncPlanAuthorizationError(decision.code, decision.reason)
        return AuthorizedSyncPlan(request.plan, request.owner_id, decision.code)


def _confirmation_request_hash(plan: SyncPlan) -> str:
    payload = f"paratranz-sync\0{plan.operation.value}\0{plan.plan_hash}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _verify_plan(plan: SyncPlan) -> None:
    if plan.compute_hash() != plan.plan_hash:
        raise SyncPlanAuthorizationError("PLAN_HASH_INVALID", "sync plan content does not match its hash")


def _sync_scope(project_id: int, namespace: SourceNamespace) -> str:
    return f"paratranz:project:{project_id}:source:{namespace.value}"
