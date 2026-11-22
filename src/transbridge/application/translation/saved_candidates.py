# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Apply an immutable selection from task storage through the project merge command."""

from dataclasses import dataclass, replace

from transbridge.application.contracts import OperationResult
from transbridge.application.io.identity import EntryKey
from transbridge.application.tasks.result_application import prior_merge_plan, select_saved_rows
from transbridge.application.tasks.storage_contracts import TaskApplication
from transbridge.application.translation.result_merge import ResultEntryChange, ResultEntryState
from transbridge.application.translation.task_result_recovery import recovery_source_context


def saved_candidate_change(row):
    before = row["before"]
    base = ResultEntryState(
        EntryKey.from_dict(row["entry_key"]),
        before["original"],
        before["translation"],
        before["stage"],
        recovery_source_context(before),
        source_locator=before.get("source_locator"),
    )
    return ResultEntryChange(base, replace(base, translation=row["text"], stage=row["stage"]))


@dataclass(frozen=True)
class CandidateApplicationResult:
    result: OperationResult
    application: TaskApplication | None
    previously_applied: bool = False
    evidence_saved: bool = False


def apply_saved_candidates(*, recovery, service, task_id, identity, candidates, policy, writer, merge, rows=None):
    """Check the stored selection, reuse a prior receipt, or commit and acknowledge once."""
    application = None
    if service is not None and candidates:
        if rows is None:
            rows = select_saved_rows(recovery, task_id, candidates)
        application = service.prepare(task_id, *identity, rows, policy)
        if (prior := prior_merge_plan(application)) is not None:
            return CandidateApplicationResult(OperationResult.completed(prior), application, previously_applied=True)
    result = merge(application.request.operation_id if application is not None else None)
    saved = False
    if result.is_success:
        if writer is not None:
            saved = writer.acknowledge(candidates, result.value, policy)
        if application is not None:
            application = service.finish(application, result.value)
    return CandidateApplicationResult(result, application, evidence_saved=saved)
