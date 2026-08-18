"""Single guarded ChangeSet commit for accepted translation candidates."""

from __future__ import annotations

from dataclasses import dataclass

from transbridge.application.contracts import (
    Diagnostic,
    ErrorCategory,
    OperationCounts,
    OperationOutcome,
    OperationResult,
    RequestContext,
)
from transbridge.application.io.identity import Provenance
from transbridge.application.io.mutation import (
    ChangeSet,
    CollectionMutationPort,
    EntryPatch,
    MutationResult,
    MutationStatus,
)
from transbridge.application.io.publish import PublishCommitGuard
from transbridge.application.io.stage_policy import StageOperation, StagePolicy, StagePolicyPort

from .candidate_checkpoint import (
    TranslationCheckpoint,
    TranslationCheckpointPort,
)
from .workload_models import CandidateSet, CandidateTranslation, canonical_hash


@dataclass(frozen=True, slots=True)
class CommitTranslationsRequest:
    candidate_set: CandidateSet
    collection: CollectionMutationPort
    context: RequestContext
    commit_guard: PublishCommitGuard
    checkpoint: TranslationCheckpointPort

    def __post_init__(self) -> None:
        if self.context.run_id != self.candidate_set.run_id:
            raise ValueError("candidate set run_id must match the trusted request context")


class CommitTranslations:
    """Validate revisions/policy, then apply one atomic ChangeSet under a terminal guard."""

    def __init__(self, stage_policy: StagePolicyPort | None = None) -> None:
        self._stage_policy = stage_policy or StagePolicy()

    def commit(self, request: CommitTranslationsRequest) -> OperationResult[dict]:
        candidate_set = request.candidate_set
        commit_id = canonical_hash(
            {
                "candidate_set": candidate_set.fingerprint,
                "run_id": candidate_set.run_id,
                "operation": "commit-translations-v1",
            }
        )
        try:
            checkpoint = request.checkpoint.load(candidate_set.run_id)
            if checkpoint is None:
                checkpoint = TranslationCheckpoint(
                    candidate_set.run_id,
                    request.context.owner_id,
                    candidate_set.spec_fingerprint,
                    candidate_set.input_fingerprint,
                )
            else:
                checkpoint.validate(
                    owner_id=request.context.owner_id,
                    spec_fingerprint=candidate_set.spec_fingerprint,
                    input_fingerprint=candidate_set.input_fingerprint,
                )
        except Exception:
            return _failed(
                candidate_set.run_id,
                "TRANSLATION_CHECKPOINT_INVALID",
                "The translation checkpoint could not authorize this commit.",
            )

        eligible: list[CandidateTranslation] = []
        conflicts: list[Diagnostic] = []
        already_committed: list[CandidateTranslation] = []
        for candidate in candidate_set.candidates:
            snapshot = request.collection.snapshot(candidate.entry_key)
            if snapshot is None:
                conflicts.append(
                    _candidate_diagnostic(
                        candidate,
                        "ENTRY_NOT_FOUND",
                        "The target entry no longer exists.",
                    )
                )
                continue
            if _already_applied(snapshot, candidate_set, candidate):
                already_committed.append(candidate)
                continue
            if snapshot.revision != candidate.before_revision:
                conflicts.append(
                    _candidate_diagnostic(
                        candidate,
                        "ENTRY_REVISION_CONFLICT",
                        "The target entry changed after candidate generation.",
                    )
                )
                continue
            decision = self._stage_policy.evaluate(
                snapshot.stage,
                snapshot.translation,
                StageOperation.AI,
                original=snapshot.original,
            )
            if not decision.include_ai:
                conflicts.append(
                    _candidate_diagnostic(
                        candidate,
                        decision.code or "STAGE_POLICY_REJECTED",
                        "The target entry is no longer eligible for automatic translation.",
                    )
                )
                continue
            eligible.append(candidate)

        if not eligible:
            if conflicts:
                return _conflict_result(candidate_set.run_id, conflicts, already_committed)
            return OperationResult.completed(
                {
                    "commit_id": commit_id,
                    "applied_keys": [],
                    "already_committed_keys": [candidate.entry_key.to_dict() for candidate in already_committed],
                    "conflicts": [],
                },
                counts=OperationCounts(skipped=len(already_committed)),
                run_id=candidate_set.run_id,
            )

        provenance = Provenance(
            candidate_set.run_id,
            request.context.owner_id,
            "translation-workload-v2",
            metadata=(
                ("candidate_set", candidate_set.fingerprint),
                ("commit_id", commit_id),
                ("stage_policy", self._stage_policy.version),
            ),
        )
        change_set = ChangeSet(
            candidate_set.run_id,
            tuple(
                EntryPatch.create(candidate.entry_key, translation=candidate.text, stage=2)
                for candidate in eligible
            ),
            tuple((candidate.entry_key, candidate.before_revision) for candidate in eligible),
            provenance,
        )
        mutation: list[MutationResult] = []

        def apply_change_set() -> None:
            mutation.append(request.collection.apply(change_set, request.context))

        try:
            guard_decision = request.commit_guard.commit(candidate_set.run_id, apply_change_set)
        except Exception:
            return _failed(
                candidate_set.run_id,
                "TRANSLATION_COMMIT_FAILED",
                "The translation ChangeSet failed at its guarded commit point.",
                count=len(eligible) + len(conflicts),
            )
        if not guard_decision.accepted:
            return OperationResult(
                OperationOutcome.CANCELLED,
                diagnostics=(
                    Diagnostic(
                        "TRANSLATION_COMMIT_REJECTED",
                        "The task runtime rejected a late or inactive translation commit.",
                        category=ErrorCategory.CANCELLED,
                        details=(("reason", guard_decision.reason),),
                    ),
                    *conflicts,
                ),
                counts=OperationCounts(
                    failed=len(conflicts),
                    skipped=len(already_committed),
                    cancelled=len(eligible),
                ),
                run_id=candidate_set.run_id,
            )
        if not mutation or mutation[0].status is not MutationStatus.APPLIED:
            diagnostics = tuple(mutation[0].diagnostics) if mutation else ()
            if not diagnostics:
                diagnostics = (
                    Diagnostic(
                        "TRANSLATION_CHANGESET_REJECTED",
                        "The collection rejected the translation ChangeSet.",
                        category=ErrorCategory.CONFLICT,
                    ),
                )
            return OperationResult(
                OperationOutcome.FAILED,
                diagnostics=(*diagnostics, *conflicts),
                counts=OperationCounts(failed=len(eligible) + len(conflicts), skipped=len(already_committed)),
                run_id=candidate_set.run_id,
            )

        checkpoint_failed = False
        try:
            request.checkpoint.save(checkpoint.mark_committed(commit_id))
        except Exception:
            checkpoint_failed = True
        value = {
            "commit_id": commit_id,
            "applied_keys": [candidate.entry_key.to_dict() for candidate in eligible],
            "already_committed_keys": [candidate.entry_key.to_dict() for candidate in already_committed],
            "conflicts": [diagnostic.to_dict() for diagnostic in conflicts],
            "mutation_status": mutation[0].status.value,
        }
        if conflicts or checkpoint_failed:
            diagnostics = tuple(conflicts)
            failed = len(conflicts)
            if checkpoint_failed:
                failed += 1
                diagnostics += (
                    Diagnostic(
                        "TRANSLATION_COMMIT_EVIDENCE_FAILED",
                        "Translations committed, but checkpoint evidence could not be advanced.",
                        category=ErrorCategory.INTERNAL,
                        retryable=True,
                    ),
                )
            return OperationResult.partial(
                value,
                counts=OperationCounts(
                    succeeded=len(eligible),
                    failed=failed,
                    skipped=len(already_committed),
                ),
                diagnostics=diagnostics,
                run_id=candidate_set.run_id,
            )
        return OperationResult.completed(
            value,
            counts=OperationCounts(succeeded=len(eligible), skipped=len(already_committed)),
            run_id=candidate_set.run_id,
        )


def _already_applied(snapshot, candidate_set: CandidateSet, candidate: CandidateTranslation) -> bool:
    if snapshot.translation != candidate.text or snapshot.stage != 2:
        return False
    return any(
        item.run_id == candidate_set.run_id
        and item.source == "translation-workload-v2"
        and dict(item.metadata).get("candidate_set") == candidate_set.fingerprint
        for item in snapshot.provenance
    )


def _candidate_diagnostic(candidate: CandidateTranslation, code: str, message: str) -> Diagnostic:
    return Diagnostic(
        code,
        message,
        category=ErrorCategory.CONFLICT,
        details=(("entry_key", candidate.entry_key.serialize()),),
    )


def _conflict_result(
    run_id: str,
    conflicts: list[Diagnostic],
    already_committed: list[CandidateTranslation],
) -> OperationResult[dict]:
    return OperationResult(
        OperationOutcome.FAILED,
        diagnostics=tuple(conflicts),
        counts=OperationCounts(failed=len(conflicts), skipped=len(already_committed)),
        run_id=run_id,
    )


def _failed(run_id: str, code: str, message: str, *, count: int = 1) -> OperationResult[dict]:
    return OperationResult(
        OperationOutcome.FAILED,
        diagnostics=(Diagnostic(code, message, category=ErrorCategory.INTERNAL),),
        counts=OperationCounts(failed=count),
        run_id=run_id,
    )
