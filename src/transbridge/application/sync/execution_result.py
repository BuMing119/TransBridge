"""Project remote/local synchronization journals into stable operation receipts."""

from dataclasses import replace

from transbridge.application.contracts import (
    Diagnostic,
    DiagnosticSeverity,
    ErrorCategory,
    OperationCounts,
    OperationOutcome,
    OperationResult,
)
from transbridge.application.io.identity import ExternalEntryRef
from transbridge.application.ports.paratranz import ExternalServiceError

from .execution_models import RetryToken, SyncItemOutcome, SyncItemStatus
from .models import SyncAction, SyncPlanItem


def item_outcome(
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
        item_id, item.entry_key, item.action, status, code, message, retryable, external_ref, remote_revision
    )


def remote_failure(item_id: str, item: SyncPlanItem, error: ExternalServiceError) -> SyncItemOutcome:
    return item_outcome(
        item_id,
        item,
        SyncItemStatus.FAILED,
        f"REMOTE_{error.category.value.upper()}",
        "无法确认此项云端写入结果，请重新检查云端实际状态后继续。",
        retryable=error.category.value in {"rate_limited", "timeout", "unavailable", "transport"},
        external_ref=item.external_ref,
    )


def append_cancelled(item_ids, checkpoint, outcomes) -> None:
    seen = {outcome.item_id for outcome in outcomes}
    for item_id, item in item_ids.items():
        if item_id not in seen and item_id not in checkpoint:
            outcomes.append(
                item_outcome(item_id, item, SyncItemStatus.CANCELLED, "SYNC_CANCELLED", "The item was not executed.")
            )


def cancel_diagnostic() -> Diagnostic:
    return Diagnostic("SYNC_CANCELLED", "The synchronization was cancelled.", category=ErrorCategory.CANCELLED)


def failed_result(run_id: str, code: str, message: str) -> OperationResult[dict]:
    return OperationResult(
        OperationOutcome.FAILED,
        diagnostics=(Diagnostic(code, message, category=ErrorCategory.CONFLICT),),
        counts=OperationCounts(failed=1),
        run_id=run_id,
    )


def external_failed_result(run_id: str, error: ExternalServiceError) -> OperationResult[dict]:
    return OperationResult(
        OperationOutcome.FAILED,
        diagnostics=(
            Diagnostic(
                f"REMOTE_{error.category.value.upper()}",
                "The remote snapshot could not be loaded.",
                category=ErrorCategory.EXTERNAL,
                retryable=error.category.value in {"rate_limited", "timeout", "unavailable", "transport"},
            ),
        ),
        counts=OperationCounts(failed=1),
        run_id=run_id,
    )


def operation_result(
    run_id: str,
    outcomes: tuple[SyncItemOutcome, ...],
    retry_token: RetryToken,
    association_error: Diagnostic | None = None,
) -> OperationResult[dict]:
    if association_error is not None:
        # These remote writes remain committed even when replay only attempted to
        # persist their associations. Keep that fact visible in a partial receipt.
        outcomes = tuple(
            replace(item, status=SyncItemStatus.SUCCEEDED, code="REMOTE_COMMITTED")
            if item.code == "RETRY_ALREADY_CONFIRMED"
            and item.action in {SyncAction.CREATE_REMOTE, SyncAction.UPDATE_REMOTE}
            else item
            for item in outcomes
        )
    counts = OperationCounts(
        succeeded=sum(item.status is SyncItemStatus.SUCCEEDED for item in outcomes),
        failed=sum(item.status is SyncItemStatus.FAILED for item in outcomes) + (association_error is not None),
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
    if association_error is not None:
        diagnostics += (association_error,)
    value = {
        "outcomes": [item.to_dict() for item in outcomes],
        "retry_token": retry_token.to_dict(),
        "deferred_count": sum(
            item.code in {"DOWNLOAD_CONFLICT_DEFERRED", "UPLOAD_CONFLICT_DEFERRED"} for item in outcomes
        ),
        "association_pending": association_error is not None,
    }
    if counts.failed or counts.cancelled:
        if counts.succeeded:
            return OperationResult.partial(value, counts=counts, diagnostics=diagnostics, run_id=run_id)
        diagnostics += (
            Diagnostic(
                "SYNC_RETRY_AVAILABLE",
                "The unsuccessful synchronization can be resumed with its retry token.",
                DiagnosticSeverity.ERROR,
                ErrorCategory.EXTERNAL,
                True,
                (("retry_token", retry_token.to_dict()),),
            ),
        )
        outcome = OperationOutcome.CANCELLED if counts.cancelled and not counts.failed else OperationOutcome.FAILED
        return OperationResult(outcome, diagnostics=diagnostics, counts=counts, run_id=run_id)
    return OperationResult.completed(value, counts=counts, run_id=run_id)
