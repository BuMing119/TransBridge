"""Transactional executor for confirmed ParaTranz synchronization plans."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from transbridge.application.contracts import OperationResult
from transbridge.application.io.identity import SourceNamespace
from transbridge.application.io.publish import PublishCommitGuard
from transbridge.application.ports.paratranz import (
    CancellationPort,
    ExternalServiceError,
    ParaTranzPort,
)
from transbridge.application.tasks.controls import TaskCleanupFailed

from .execution_models import RetryToken, SyncItemOutcome, SyncItemStatus, sync_item_id
from .execution_result import (
    append_cancelled as _append_cancelled,
    cancel_diagnostic as _cancel_diagnostic,
    external_failed_result as _external_failed_result,
    failed_result as _failed_result,
    item_outcome as _outcome,
    operation_result as _operation_result,
    remote_failure as _remote_failure,
)
from .local_candidate import RemoteSourceIndex, build_local_candidate
from .local_transaction import (
    CallbackLocalSyncUnitOfWork as CallbackLocalSyncUnitOfWork,
    LocalSyncTransactionPort as LocalSyncTransactionPort,
    LocalSyncUnitOfWorkPort as LocalSyncUnitOfWorkPort,
)
from .models import (
    EntrySummary,
    LocalEntrySnapshot,
    RemoteEntrySnapshot,
    SyncAction,
    SyncOperation,
    SyncPlanItem,
)
from .planner import SyncPlanner
from .remote_execution import RemoteTargetIndex, execute_remote
from .upload_associations import commit_upload_associations
from .use_case import AuthorizedSyncPlan, RemoteSyncSnapshotPort

_logger = logging.getLogger(__name__)


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
        remote_targets = RemoteTargetIndex(remote_entries)
        remote_sources = RemoteSourceIndex(remote_entries)
        outcomes: list[SyncItemOutcome] = []
        pending_local: list[tuple[str, SyncPlanItem]] = []
        cancelled = False

        for item_id, item in item_ids.items():
            prior = checkpoint.get(item_id)
            if prior is not None:
                deferred = prior.code in {"DOWNLOAD_CONFLICT_DEFERRED", "UPLOAD_CONFLICT_DEFERRED"}
                outcomes.append(
                    SyncItemOutcome(
                        item_id,
                        item.entry_key,
                        item.action,
                        SyncItemStatus.SKIPPED,
                        prior.code if deferred else "RETRY_ALREADY_CONFIRMED",
                        prior.message if deferred else "The item was already confirmed by the retry checkpoint.",
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
                deferred = plan.operation in {SyncOperation.DOWNLOAD, SyncOperation.UPLOAD}
                outcomes.append(
                    _outcome(
                        item_id,
                        item,
                        SyncItemStatus.SKIPPED if deferred else SyncItemStatus.FAILED,
                        f"{plan.operation.value.upper()}_CONFLICT_DEFERRED" if deferred else "UNRESOLVED_CONFLICT",
                        "The conflicting item was preserved pending review."
                        if deferred
                        else "The item has an unresolved conflict.",
                    )
                )
                continue
            if item.action in {SyncAction.CREATE_LOCAL, SyncAction.UPDATE_LOCAL, SyncAction.DELETE_LOCAL}:
                pending_local.append((item_id, item))
                continue
            try:
                _check_cancelled(request.cancellation)
                result = execute_remote(self._remote, request, item, local_map, remote_targets)
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
                _logger.exception("ParaTranz remote execution failed (run_id=%s, item_id=%s)", request.run_id, item_id)
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
                remote_sources,
                pending_local,
                checkpoint,
            )
            outcomes.extend(local_outcomes)

        association_error = None
        if plan.operation is SyncOperation.UPLOAD:
            association_error = commit_upload_associations(self._local_uow, request, checkpoint, item_ids)
        token_outcomes = dict(checkpoint)
        for outcome in outcomes:
            if outcome.status.confirmed or outcome.status is SyncItemStatus.FAILED:
                token_outcomes[outcome.item_id] = outcome
        retry_token = RetryToken.issue(
            plan_hash=plan.plan_hash,
            owner_id=request.authorized_plan.owner_id,
            outcomes=tuple(token_outcomes.values()),
        )
        return _operation_result(request.run_id, tuple(outcomes), retry_token, association_error)

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
        remote_targets = RemoteTargetIndex(remote_entries)
        remote_sources = RemoteSourceIndex(remote_entries)
        for item_id, item in item_ids.items():
            if item_id in confirmed or item.action in {SyncAction.SKIP, SyncAction.CONFLICT}:
                continue
            if item.action in {SyncAction.CREATE_REMOTE, SyncAction.UPDATE_REMOTE, SyncAction.DELETE_REMOTE}:
                if not _summary_matches(item.after, local.get(item.entry_key), local_side=True):
                    raise RuntimeError("local source changed")
                if not _summary_matches(item.before, remote_targets.get_for_item(item), local_side=False):
                    raise RuntimeError("remote target changed")
            else:
                if not _summary_matches(item.after, remote_sources.get_for_item(item), local_side=False):
                    raise RuntimeError("remote source changed")
                if not _summary_matches(item.before, local.get(item.entry_key), local_side=True):
                    raise RuntimeError("local target changed")

    def _commit_local(
        self,
        request: ExecuteSyncRequest,
        remote_sources: RemoteSourceIndex,
        pending: list[tuple[str, SyncPlanItem]],
        checkpoint: dict[str, SyncItemOutcome],
    ) -> list[SyncItemOutcome]:
        transaction = None
        committed = False
        try:
            candidate = build_local_candidate(
                request.current_local_entries, remote_sources, tuple(item for _, item in pending)
            )
            _check_cancelled(request.cancellation)
            expected_hash, _ = self._planner.snapshot_hashes(request.current_local_entries, ())
            transaction = self._local_uow.begin(expected_snapshot_hash=expected_hash)
            transaction.stage(candidate)
            _check_cancelled(request.cancellation)
            decision = transaction.commit(request.run_id, request.commit_guard)
            if not decision.accepted:
                _check_cancelled(request.cancellation)
                raise _CommitRejected(decision.reason or "commit_rejected")
            committed = True
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
            _logger.exception("ParaTranz local transaction failed (run_id=%s)", request.run_id)
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
        finally:
            if transaction is not None and not committed:
                try:
                    transaction.rollback()
                except Exception as exc:
                    raise TaskCleanupFailed(
                        f"ParaTranz local rollback failed (run_id={request.run_id}): {exc}"
                    ) from exc
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
