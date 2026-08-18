"""Transactional executor for confirmed ParaTranz synchronization plans."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from transbridge.application.contracts import (
    Diagnostic,
    DiagnosticSeverity,
    ErrorCategory,
    OperationCounts,
    OperationOutcome,
    OperationResult,
)
from transbridge.application.io.identity import EntryRevision, ExternalEntryRef, SourceNamespace
from transbridge.application.io.publish import CommitDecision, PublishCommitGuard
from transbridge.application.ports.paratranz import (
    CancellationPort,
    ExternalServiceError,
    ParaTranzEntry,
    ParaTranzPort,
)

from .execution_models import RetryToken, SyncItemOutcome, SyncItemStatus, sync_item_id
from .models import (
    EntrySummary,
    LocalEntrySnapshot,
    RemoteEntrySnapshot,
    SyncAction,
    SyncPlanItem,
    canonical_hash,
)
from .planner import SyncPlanner
from .use_case import AuthorizedSyncPlan, RemoteSyncSnapshotPort


class LocalSyncTransactionPort(Protocol):
    """One isolated candidate aggregate transaction."""

    def stage(self, entries: tuple[LocalEntrySnapshot, ...]) -> None: ...

    def commit(self, run_id: str, guard: PublishCommitGuard) -> CommitDecision: ...

    def rollback(self) -> None: ...


class LocalSyncUnitOfWorkPort(Protocol):
    def begin(self, *, expected_snapshot_hash: str) -> LocalSyncTransactionPort: ...


@dataclass(frozen=True, slots=True)
class ExecuteSyncRequest:
    authorized_plan: AuthorizedSyncPlan
    project_id: int
    namespace: SourceNamespace
    current_local_entries: tuple[LocalEntrySnapshot, ...]
    run_id: str
    commit_guard: PublishCommitGuard
    remote_limit: int = 100_000
    cancellation: CancellationPort | None = None
    retry_token: RetryToken | None = None

    def __post_init__(self) -> None:
        if isinstance(self.project_id, bool) or not isinstance(self.project_id, int) or self.project_id < 1:
            raise ValueError("project_id must be a positive integer")
        if not self.run_id.strip():
            raise ValueError("sync run_id must not be empty")
        if isinstance(self.remote_limit, bool) or not isinstance(self.remote_limit, int) or self.remote_limit < 1:
            raise ValueError("remote_limit must be a positive integer")
        object.__setattr__(self, "current_local_entries", tuple(self.current_local_entries))
        if any(entry.entry_key.namespace != self.namespace for entry in self.current_local_entries):
            raise ValueError("all current local entries must belong to the requested namespace")


class CallbackLocalSyncUnitOfWork:
    """Small adapter for repositories that expose atomic aggregate load/replace callbacks."""

    def __init__(
        self,
        load: Callable[[], tuple[LocalEntrySnapshot, ...]],
        replace: Callable[[tuple[LocalEntrySnapshot, ...]], None],
    ) -> None:
        self._load = load
        self._replace = replace

    def begin(self, *, expected_snapshot_hash: str) -> LocalSyncTransactionPort:
        return _CallbackTransaction(self._load, self._replace, expected_snapshot_hash)


class _CallbackTransaction:
    def __init__(self, load, replace, expected_snapshot_hash: str) -> None:
        self._load = load
        self._replace = replace
        self._expected_snapshot_hash = expected_snapshot_hash
        self._candidate: tuple[LocalEntrySnapshot, ...] | None = None
        self._closed = False

    def stage(self, entries: tuple[LocalEntrySnapshot, ...]) -> None:
        if self._closed or self._candidate is not None:
            raise RuntimeError("local sync transaction is not stageable")
        self._candidate = tuple(entries)

    def commit(self, run_id: str, guard: PublishCommitGuard) -> CommitDecision:
        if self._closed or self._candidate is None:
            raise RuntimeError("local sync transaction has no staged candidate")

        def mutation() -> None:
            current_hash, _ = SyncPlanner().snapshot_hashes(self._load(), ())
            if current_hash != self._expected_snapshot_hash:
                raise RuntimeError("local aggregate changed before commit")
            self._replace(self._candidate or ())

        try:
            return guard.commit(run_id, mutation)
        finally:
            self._closed = True

    def rollback(self) -> None:
        self._candidate = None
        self._closed = True


class ParaTranzSyncExecutor:
    """Execute only authorized immutable plans and atomically publish local changes."""

    def __init__(
        self,
        remote: ParaTranzPort,
        remote_snapshots: RemoteSyncSnapshotPort,
        local_uow: LocalSyncUnitOfWorkPort,
        *,
        planner: SyncPlanner | None = None,
    ) -> None:
        self._remote = remote
        self._remote_snapshots = remote_snapshots
        self._local_uow = local_uow
        self._planner = planner or SyncPlanner()

    def execute(self, request: ExecuteSyncRequest) -> OperationResult[dict]:
        plan = request.authorized_plan.plan
        if not request.authorized_plan.owner_id.strip():
            return _failed_result(request.run_id, "INVALID_SYNC_OWNER", "The sync owner is invalid.")
        expected_scope = f"paratranz:project:{request.project_id}:source:{request.namespace.value}"
        if plan.compute_hash() != plan.plan_hash or plan.scope != expected_scope:
            return _failed_result(request.run_id, "INVALID_AUTHORIZED_PLAN", "The authorized sync plan is invalid.")
        if plan.requires_confirmation and request.authorized_plan.confirmation_code != "CONFIRMED":
            return _failed_result(
                request.run_id,
                "SYNC_CONFIRMATION_REQUIRED",
                "The destructive sync plan has not been confirmed.",
            )
        item_ids = {sync_item_id(plan.plan_hash, item.action, item.entry_key): item for item in plan.items}
        if len(item_ids) != len(plan.items):
            return _failed_result(request.run_id, "DUPLICATE_PLAN_ITEM", "The sync plan contains duplicate items.")
        checkpoint: dict[str, SyncItemOutcome] = {}
        if request.retry_token is not None:
            try:
                request.retry_token.validate_binding(
                    plan_hash=plan.plan_hash,
                    owner_id=request.authorized_plan.owner_id,
                    valid_item_ids=frozenset(item_ids),
                )
            except (TypeError, ValueError):
                return _failed_result(request.run_id, "INVALID_RETRY_TOKEN", "The retry token is invalid.")
            checkpoint.update(
                (outcome.item_id, outcome) for outcome in request.retry_token.outcomes if outcome.status.confirmed
            )
        try:
            _check_cancelled(request.cancellation)
            remote_entries = self._remote_snapshots.fetch(
                request.project_id,
                request.namespace,
                limit=request.remote_limit,
                cancellation=request.cancellation,
            )
            _check_cancelled(request.cancellation)
            self._verify_freshness(request, remote_entries, item_ids, frozenset(checkpoint))
        except _SyncCancelled:
            return OperationResult.cancelled(_cancel_diagnostic(), run_id=request.run_id)
        except ExternalServiceError as exc:
            if exc.category.value == "cancelled" or (
                request.cancellation is not None and request.cancellation.is_cancelled
            ):
                return OperationResult.cancelled(_cancel_diagnostic(), run_id=request.run_id)
            return _external_failed_result(request.run_id, exc)
        except Exception:
            if request.cancellation is not None and request.cancellation.is_cancelled:
                return OperationResult.cancelled(_cancel_diagnostic(), run_id=request.run_id)
            return _failed_result(request.run_id, "STALE_SYNC_PLAN", "The sync snapshots changed before execution.")

        local_map = {entry.entry_key: entry for entry in request.current_local_entries}
        remote_map = {entry.entry_key: entry for entry in remote_entries}
        outcomes: list[SyncItemOutcome] = []
        pending_local: list[tuple[str, SyncPlanItem]] = []
        cancelled = False

        for item_id, item in item_ids.items():
            prior = checkpoint.get(item_id)
            if prior is not None:
                outcomes.append(
                    SyncItemOutcome(
                        item_id,
                        item.entry_key,
                        item.action,
                        SyncItemStatus.SKIPPED,
                        "RETRY_ALREADY_CONFIRMED",
                        "The item was already confirmed by the retry checkpoint.",
                        external_ref=prior.external_ref,
                        remote_revision=prior.remote_revision,
                    )
                )
                continue
            if item.action is SyncAction.SKIP:
                outcome = _outcome(
                    item_id,
                    item,
                    SyncItemStatus.SKIPPED,
                    "PLAN_SKIPPED",
                    "The plan skipped this item.",
                )
                outcomes.append(outcome)
                checkpoint[item_id] = outcome
                continue
            if item.action is SyncAction.CONFLICT:
                outcomes.append(
                    _outcome(
                        item_id,
                        item,
                        SyncItemStatus.FAILED,
                        "UNRESOLVED_CONFLICT",
                        "The item has an unresolved conflict.",
                    )
                )
                continue
            if item.action in {SyncAction.CREATE_LOCAL, SyncAction.UPDATE_LOCAL, SyncAction.DELETE_LOCAL}:
                pending_local.append((item_id, item))
                continue
            try:
                _check_cancelled(request.cancellation)
                result = self._execute_remote(request, item, local_map)
                outcome = _outcome(
                    item_id,
                    item,
                    SyncItemStatus.SUCCEEDED,
                    "REMOTE_COMMITTED",
                    "The remote item was committed.",
                    external_ref=result[0],
                    remote_revision=result[1],
                )
                outcomes.append(outcome)
                checkpoint[item_id] = outcome
                if request.cancellation is not None and request.cancellation.is_cancelled:
                    cancelled = True
                    break
            except _SyncCancelled:
                cancelled = True
                break
            except ExternalServiceError as exc:
                if exc.category.value == "cancelled" or (
                    request.cancellation is not None and request.cancellation.is_cancelled
                ):
                    cancelled = True
                    break
                outcomes.append(_remote_failure(item_id, item, exc))
            except Exception:
                if request.cancellation is not None and request.cancellation.is_cancelled:
                    cancelled = True
                    break
                outcomes.append(
                    _outcome(
                        item_id,
                        item,
                        SyncItemStatus.FAILED,
                        "REMOTE_OPERATION_FAILED",
                        "The remote operation failed.",
                    )
                )

        if cancelled:
            _append_cancelled(item_ids, checkpoint, outcomes)
        elif pending_local:
            local_outcomes = self._commit_local(
                request,
                remote_map,
                pending_local,
                checkpoint,
            )
            outcomes.extend(local_outcomes)

        token_outcomes = dict(checkpoint)
        for outcome in outcomes:
            if outcome.status.confirmed or outcome.status is SyncItemStatus.FAILED:
                token_outcomes[outcome.item_id] = outcome
        retry_token = RetryToken.issue(
            plan_hash=plan.plan_hash,
            owner_id=request.authorized_plan.owner_id,
            outcomes=tuple(token_outcomes.values()),
        )
        return _operation_result(request.run_id, tuple(outcomes), retry_token)

    def _verify_freshness(
        self,
        request: ExecuteSyncRequest,
        remote_entries: tuple[RemoteEntrySnapshot, ...],
        item_ids: dict[str, SyncPlanItem],
        confirmed: frozenset[str],
    ) -> None:
        plan = request.authorized_plan.plan
        if not confirmed:
            hashes = self._planner.snapshot_hashes(request.current_local_entries, remote_entries)
            if hashes != (plan.local_snapshot_hash, plan.remote_snapshot_hash):
                raise RuntimeError("sync snapshots changed")
            return
        local = {entry.entry_key: entry for entry in request.current_local_entries}
        remote = {entry.entry_key: entry for entry in remote_entries}
        for item_id, item in item_ids.items():
            if item_id in confirmed or item.action in {SyncAction.SKIP, SyncAction.CONFLICT}:
                continue
            if item.action in {SyncAction.CREATE_REMOTE, SyncAction.UPDATE_REMOTE, SyncAction.DELETE_REMOTE}:
                if not _summary_matches(item.after, local.get(item.entry_key), local_side=True):
                    raise RuntimeError("local source changed")
                if not _summary_matches(item.before, remote.get(item.entry_key), local_side=False):
                    raise RuntimeError("remote target changed")
            else:
                if not _summary_matches(item.after, remote.get(item.entry_key), local_side=False):
                    raise RuntimeError("remote source changed")
                if not _summary_matches(item.before, local.get(item.entry_key), local_side=True):
                    raise RuntimeError("local target changed")

    def _execute_remote(
        self,
        request: ExecuteSyncRequest,
        item: SyncPlanItem,
        local_map: dict,
    ) -> tuple[ExternalEntryRef | None, str | None]:
        if item.action is SyncAction.DELETE_REMOTE:
            reference = item.external_ref
            if reference is None or isinstance(reference.opaque_id, bool) or not isinstance(reference.opaque_id, int):
                raise ValueError("ParaTranz delete requires an integer remote id")
            self._remote.delete_entry(
                request.project_id,
                reference.opaque_id,
                cancellation=request.cancellation,
            )
            return reference, "deleted"
        local = local_map.get(item.entry_key)
        if local is None:
            raise ValueError("local source entry is missing")
        result = self._remote.upsert_entry(
            request.project_id,
            ParaTranzEntry(
                None,
                local.entry_key.local_key,
                local.original,
                local.translation,
                local.context,
                local.stage,
            ),
            force_overwrite=item.action is SyncAction.UPDATE_REMOTE,
            cancellation=request.cancellation,
        )
        reference = None
        if result.remote_id is not None:
            reference = ExternalEntryRef("paratranz", f"project:{request.project_id}", result.remote_id)
        return reference, _remote_revision(result)

    def _commit_local(
        self,
        request: ExecuteSyncRequest,
        remote_map: dict,
        pending: list[tuple[str, SyncPlanItem]],
        checkpoint: dict[str, SyncItemOutcome],
    ) -> list[SyncItemOutcome]:
        candidate = {entry.entry_key: entry for entry in request.current_local_entries}
        try:
            for _, item in pending:
                remote = remote_map.get(item.entry_key)
                if item.action is SyncAction.DELETE_LOCAL:
                    candidate.pop(item.entry_key, None)
                    continue
                if remote is None or remote.deleted:
                    raise RuntimeError("remote source entry is unavailable")
                current = candidate.get(item.entry_key)
                revision = EntryRevision() if current is None else current.revision.next()
                candidate[item.entry_key] = LocalEntrySnapshot(
                    item.entry_key,
                    revision,
                    remote.original,
                    remote.translation,
                    remote.context,
                    remote.stage,
                    remote.external_ref,
                )
            _check_cancelled(request.cancellation)
            expected_hash, _ = self._planner.snapshot_hashes(request.current_local_entries, ())
            transaction = self._local_uow.begin(expected_snapshot_hash=expected_hash)
            transaction.stage(tuple(sorted(candidate.values(), key=lambda entry: entry.entry_key)))
            _check_cancelled(request.cancellation)
            decision = transaction.commit(request.run_id, request.commit_guard)
            if not decision.accepted:
                raise _CommitRejected(decision.reason or "commit_rejected")
        except _SyncCancelled:
            return [
                _outcome(
                    item_id,
                    item,
                    SyncItemStatus.CANCELLED,
                    "SYNC_CANCELLED",
                    "The local item was not committed.",
                )
                for item_id, item in pending
            ]
        except Exception:
            return [
                _outcome(
                    item_id,
                    item,
                    SyncItemStatus.FAILED,
                    "LOCAL_TRANSACTION_FAILED",
                    "The local aggregate was not committed.",
                    retryable=True,
                )
                for item_id, item in pending
            ]
        results = []
        for item_id, item in pending:
            outcome = _outcome(
                item_id,
                item,
                SyncItemStatus.SUCCEEDED,
                "LOCAL_AGGREGATE_COMMITTED",
                "The local item was committed in the aggregate transaction.",
                external_ref=item.external_ref,
            )
            checkpoint[item_id] = outcome
            results.append(outcome)
        return results


class _SyncCancelled(RuntimeError):
    pass


class _CommitRejected(RuntimeError):
    pass


def _check_cancelled(cancellation: CancellationPort | None) -> None:
    if cancellation is not None and cancellation.is_cancelled:
        raise _SyncCancelled


def _summary_matches(summary: EntrySummary | None, entry, *, local_side: bool) -> bool:
    if summary is None:
        return entry is None
    if entry is None:
        return False
    actual = EntrySummary.from_local(entry) if local_side else EntrySummary.from_remote(entry)
    return actual == summary


def _remote_revision(entry: ParaTranzEntry) -> str:
    return canonical_hash({
        "id": entry.remote_id,
        "key": entry.key,
        "original": entry.original,
        "translation": entry.translation,
        "context": entry.context,
        "stage": entry.stage,
    })


def _outcome(
    item_id: str,
    item: SyncPlanItem,
    status: SyncItemStatus,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    external_ref: ExternalEntryRef | None = None,
    remote_revision: str | None = None,
) -> SyncItemOutcome:
    return SyncItemOutcome(
        item_id,
        item.entry_key,
        item.action,
        status,
        code,
        message,
        retryable,
        external_ref,
        remote_revision,
    )


def _remote_failure(item_id: str, item: SyncPlanItem, error: ExternalServiceError) -> SyncItemOutcome:
    retryable = error.category.value in {"rate_limited", "timeout", "unavailable", "transport"}
    return _outcome(
        item_id,
        item,
        SyncItemStatus.FAILED,
        f"REMOTE_{error.category.value.upper()}",
        "The remote operation was not committed.",
        retryable=retryable,
        external_ref=item.external_ref,
    )


def _append_cancelled(item_ids, checkpoint, outcomes) -> None:
    seen = {outcome.item_id for outcome in outcomes}
    for item_id, item in item_ids.items():
        if item_id in seen or item_id in checkpoint:
            continue
        outcomes.append(
            _outcome(item_id, item, SyncItemStatus.CANCELLED, "SYNC_CANCELLED", "The item was not executed.")
        )


def _cancel_diagnostic() -> Diagnostic:
    return Diagnostic(
        "SYNC_CANCELLED",
        "The synchronization was cancelled.",
        category=ErrorCategory.CANCELLED,
    )


def _failed_result(run_id: str, code: str, message: str) -> OperationResult[dict]:
    diagnostic = Diagnostic(code, message, category=ErrorCategory.CONFLICT)
    return OperationResult(
        OperationOutcome.FAILED,
        diagnostics=(diagnostic,),
        counts=OperationCounts(failed=1),
        run_id=run_id,
    )


def _external_failed_result(run_id: str, error: ExternalServiceError) -> OperationResult[dict]:
    retryable = error.category.value in {"rate_limited", "timeout", "unavailable", "transport"}
    diagnostic = Diagnostic(
        f"REMOTE_{error.category.value.upper()}",
        "The remote snapshot could not be loaded.",
        category=ErrorCategory.EXTERNAL,
        retryable=retryable,
    )
    return OperationResult(
        OperationOutcome.FAILED,
        diagnostics=(diagnostic,),
        counts=OperationCounts(failed=1),
        run_id=run_id,
    )


def _operation_result(
    run_id: str,
    outcomes: tuple[SyncItemOutcome, ...],
    retry_token: RetryToken,
) -> OperationResult[dict]:
    counts = OperationCounts(
        succeeded=sum(item.status is SyncItemStatus.SUCCEEDED for item in outcomes),
        failed=sum(item.status is SyncItemStatus.FAILED for item in outcomes),
        skipped=sum(item.status is SyncItemStatus.SKIPPED for item in outcomes),
        cancelled=sum(item.status is SyncItemStatus.CANCELLED for item in outcomes),
    )
    diagnostics = tuple(
        Diagnostic(
            item.code,
            item.message,
            DiagnosticSeverity.ERROR,
            ErrorCategory.CANCELLED if item.status is SyncItemStatus.CANCELLED else ErrorCategory.EXTERNAL,
            item.retryable,
            (("action", item.action.value), ("entry_key", item.entry_key.serialize())),
        )
        for item in outcomes
        if item.status in {SyncItemStatus.FAILED, SyncItemStatus.CANCELLED}
    )
    value = {
        "outcomes": [item.to_dict() for item in outcomes],
        "retry_token": retry_token.to_dict(),
    }
    if counts.failed or counts.cancelled:
        if counts.succeeded:
            return OperationResult.partial(value, counts=counts, diagnostics=diagnostics, run_id=run_id)
        retry_diagnostic = Diagnostic(
            "SYNC_RETRY_AVAILABLE",
            "The unsuccessful synchronization can be resumed with its retry token.",
            DiagnosticSeverity.ERROR,
            ErrorCategory.EXTERNAL,
            True,
            (("retry_token", retry_token.to_dict()),),
        )
        diagnostics = diagnostics + (retry_diagnostic,)
        outcome = OperationOutcome.CANCELLED if counts.cancelled and not counts.failed else OperationOutcome.FAILED
        return OperationResult(outcome, diagnostics=diagnostics, counts=counts, run_id=run_id)
    return OperationResult.completed(value, counts=counts, run_id=run_id)
