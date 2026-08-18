"""Production orchestration for candidate-only post-processing and one commit."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from transbridge.application.contracts import OperationOutcome, OperationResult, RequestContext
from transbridge.application.io.identity import Provenance
from transbridge.application.io.mutation import CollectionMutationPort
from transbridge.application.io.publish import PublishCommitGuard

from .candidate_checkpoint import TranslationCheckpointPort
from .commit import CommitTranslations, CommitTranslationsRequest
from .models import TranslationAction
from .postprocess import PostProcessWorkload, ReportSnapshot
from .workload_models import (
    CandidateSet,
    CandidateTranslation,
    TranslationInput,
    canonical_hash,
    translation_input_fingerprint,
)


@dataclass(frozen=True, slots=True)
class PostProcessExecutionResult:
    report_result: OperationResult[ReportSnapshot]
    commit_result: OperationResult[dict] | None

    @property
    def outcome(self) -> OperationOutcome:
        outcomes = [self.report_result.outcome]
        if self.commit_result is not None:
            outcomes.append(self.commit_result.outcome)
        for outcome in (
            OperationOutcome.FAILED,
            OperationOutcome.CANCELLED,
            OperationOutcome.PARTIAL,
        ):
            if outcome in outcomes:
                return outcome
        return OperationOutcome.COMPLETED


class PostProcessExecutionService:
    """Run immutable stages, then commit accepted changed values exactly once."""

    def __init__(self, workload: PostProcessWorkload) -> None:
        self._workload = workload

    def execute(
        self,
        *,
        run_id: str,
        entries: tuple[TranslationInput, ...],
        collection: CollectionMutationPort,
        context: RequestContext,
        commit_guard: PublishCommitGuard,
        commit_checkpoint: TranslationCheckpointPort,
        is_cancelled: Callable[[], bool] = lambda: False,
        run_spec_summary: dict[str, object] | None = None,
    ) -> PostProcessExecutionResult:
        if context.run_id != run_id:
            raise ValueError("post-process context run_id mismatch")
        report_result = self._workload.run(
            run_id,
            entries,
            is_cancelled=is_cancelled,
            owner_id=context.owner_id,
            expected_revisions={entry.entry_key: entry.revision for entry in entries},
            run_spec_summary=run_spec_summary,
        )
        report = report_result.value
        if report is None or report_result.outcome not in {
            OperationOutcome.COMPLETED,
            OperationOutcome.PARTIAL,
        }:
            return PostProcessExecutionResult(report_result, None)

        report_hash = report.fingerprint
        batch_id = canonical_hash({"run_id": run_id, "report": report_hash, "operation": "postprocess"})
        candidates = tuple(
            CandidateTranslation(
                run_id,
                candidate.entry_key,
                candidate.before_revision,
                TranslationAction.POLISH,
                candidate.text,
                batch_id,
                1,
                report_hash,
                Provenance(
                    run_id,
                    context.owner_id,
                    "postprocess-workload-v2",
                    metadata=(("report", report_hash), ("phases", ",".join(candidate.phases))),
                ),
            )
            for candidate in report.candidates
            if candidate.accepted and candidate.text != candidate.before_text
        )
        if not candidates:
            return PostProcessExecutionResult(report_result, None)
        candidate_set = CandidateSet(
            run_id,
            canonical_hash(run_spec_summary or {"operation": "postprocess"}),
            translation_input_fingerprint(entries),
            candidates,
            (),
        )
        commit_result = CommitTranslations().commit(
            CommitTranslationsRequest(
                candidate_set,
                collection,
                context,
                commit_guard,
                commit_checkpoint,
            )
        )
        return PostProcessExecutionResult(report_result, commit_result)
