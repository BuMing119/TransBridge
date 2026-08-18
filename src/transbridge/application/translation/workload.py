"""Side-effect-free AI translation workload producing checkpointed candidates."""

from __future__ import annotations

from dataclasses import dataclass, replace
import time

from transbridge.application.contracts import (
    Diagnostic,
    DiagnosticSeverity,
    ErrorCategory,
    OperationCounts,
    OperationOutcome,
    OperationResult,
)
from transbridge.application.io.identity import Provenance
from transbridge.application.ports.paratranz import CancellationPort

from .candidate_checkpoint import (
    TranslationCheckpoint,
    TranslationCheckpointPort,
)
from .models import ActionPlan, ContextPlan, TranslationAction, TranslationRunSpec
from .workload_models import (
    CandidateSet,
    CandidateTranslation,
    TranslationBatchOutcome,
    TranslationBatchRequest,
    TranslationBatchResponse,
    TranslationBatchStatus,
    TranslationInput,
    TranslationLlmPort,
    TranslationServiceError,
    translation_batch_id,
    translation_input_fingerprint,
)


@dataclass(frozen=True, slots=True)
class TranslationWorkloadRequest:
    run_spec: TranslationRunSpec
    action_plan: ActionPlan
    context_plan: ContextPlan
    entries: tuple[TranslationInput, ...]
    owner_id: str
    checkpoint: TranslationCheckpointPort
    cancellation: CancellationPort | None = None
    max_retries: int = 2
    retry_backoff_seconds: float = 0.1

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))
        if self.run_spec.run_id.strip() == "" or not self.owner_id.strip():
            raise ValueError("translation workload run and owner identities must not be empty")
        if self.action_plan.scope != self.run_spec.scope:
            raise ValueError("ActionPlan scope must exactly match TranslationRunSpec scope")
        keys = tuple(entry.entry_key for entry in self.entries)
        if len(keys) != len(set(keys)) or set(keys) != set(self.run_spec.scope):
            raise ValueError("translation workload entries must uniquely cover the run scope")
        actionable = {
            assignment.key
            for assignment in self.action_plan.assignments
            if assignment.action is not TranslationAction.SKIP
        }
        if len(self.context_plan.keys) != len(set(self.context_plan.keys)) or set(self.context_plan.keys) != actionable:
            raise ValueError("ContextPlan must uniquely cover all actionable entries")
        if isinstance(self.max_retries, bool) or not isinstance(self.max_retries, int) or self.max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")
        if self.retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must not be negative")


class TranslationWorkload:
    """Execute bounded ordered batches without mutating the formal Collection."""

    def __init__(self, llm: TranslationLlmPort) -> None:
        self._llm = llm

    def run(self, request: TranslationWorkloadRequest) -> OperationResult[dict]:
        spec = request.run_spec
        input_fingerprint = translation_input_fingerprint(request.entries)
        try:
            checkpoint = request.checkpoint.load(spec.run_id)
            if checkpoint is None:
                checkpoint = TranslationCheckpoint(
                    spec.run_id,
                    request.owner_id,
                    spec.fingerprint,
                    input_fingerprint,
                )
            else:
                checkpoint.validate(
                    owner_id=request.owner_id,
                    spec_fingerprint=spec.fingerprint,
                    input_fingerprint=input_fingerprint,
                )
        except Exception:
            return _failed(
                spec.run_id,
                "TRANSLATION_CHECKPOINT_INVALID",
                "The translation checkpoint is invalid or belongs to another workload.",
            )

        by_key = {entry.entry_key: entry for entry in request.entries}
        action_by_key = {assignment.key: assignment.action for assignment in request.action_plan.assignments}
        candidates = {candidate.entry_key: candidate for candidate in checkpoint.candidates}
        outcomes = {outcome.batch_id: outcome for outcome in checkpoint.outcomes}
        final_keys = _restored_final_keys(candidates, outcomes)
        cancelled = False

        for context_batch in request.context_plan.batches:
            if _cancelled(request.cancellation):
                cancelled = True
                break
            translate_entries = tuple(
                by_key[key]
                for key in context_batch.keys
                if action_by_key[key] in {TranslationAction.TRANSLATE, TranslationAction.BOTH}
            )
            if translate_entries:
                result = self._run_batch(
                    request,
                    checkpoint,
                    context_batch.round_number,
                    context_batch.category,
                    context_batch.quest_id,
                    TranslationAction.TRANSLATE,
                    translate_entries,
                    action_by_key,
                )
                checkpoint, outcome, batch_candidates = result
                outcomes[outcome.batch_id] = outcome
                candidates.update((candidate.entry_key, candidate) for candidate in batch_candidates)
                if outcome.status in {TranslationBatchStatus.ACCEPTED, TranslationBatchStatus.RESTORED}:
                    final_keys.update(
                        key for key in outcome.entry_keys if action_by_key[key] is TranslationAction.TRANSLATE
                    )
                if outcome.status is TranslationBatchStatus.CANCELLED:
                    cancelled = True
                    break

            polish_inputs: list[TranslationInput] = []
            missing_polish: list[TranslationInput] = []
            for key in context_batch.keys:
                action = action_by_key[key]
                if action not in {TranslationAction.POLISH, TranslationAction.BOTH}:
                    continue
                source = by_key[key]
                candidate = candidates.get(key)
                current_text = candidate.text if action is TranslationAction.BOTH and candidate else source.translation
                if not current_text:
                    missing_polish.append(source)
                    continue
                polish_inputs.append(replace(source, translation=current_text))
            if missing_polish:
                outcome = _missing_polish_outcome(
                    spec,
                    tuple(missing_polish),
                    context_batch.round_number,
                    context_batch.category,
                    context_batch.quest_id,
                )
                outcomes[outcome.batch_id] = outcome
                final_keys.difference_update(outcome.entry_keys)
            if polish_inputs:
                result = self._run_batch(
                    request,
                    checkpoint,
                    context_batch.round_number,
                    context_batch.category,
                    context_batch.quest_id,
                    TranslationAction.POLISH,
                    tuple(polish_inputs),
                    action_by_key,
                )
                checkpoint, outcome, batch_candidates = result
                outcomes[outcome.batch_id] = outcome
                candidates.update((candidate.entry_key, candidate) for candidate in batch_candidates)
                if outcome.status in {TranslationBatchStatus.ACCEPTED, TranslationBatchStatus.RESTORED}:
                    final_keys.update(outcome.entry_keys)
                else:
                    final_keys.difference_update(outcome.entry_keys)
                if outcome.status is TranslationBatchStatus.CANCELLED:
                    cancelled = True
                    break

        final_candidates = tuple(
            sorted(
                (candidate for key, candidate in candidates.items() if key in final_keys),
                key=lambda candidate: candidate.entry_key,
            )
        )
        candidate_set = CandidateSet(
            spec.run_id,
            spec.fingerprint,
            input_fingerprint,
            final_candidates,
            tuple(sorted(outcomes.values(), key=lambda outcome: outcome.batch_id)),
        )
        return _workload_result(request, candidate_set, cancelled=cancelled)

    def _run_batch(
        self,
        request: TranslationWorkloadRequest,
        checkpoint: TranslationCheckpoint,
        round_number: int,
        category: str,
        quest_id: str,
        action: TranslationAction,
        entries: tuple[TranslationInput, ...],
        action_by_key: dict,
    ) -> tuple[TranslationCheckpoint, TranslationBatchOutcome, tuple[CandidateTranslation, ...]]:
        batch_id = translation_batch_id(
            request.run_spec,
            action,
            entries,
            round_number=round_number,
            category=category,
            quest_id=quest_id,
        )
        if batch_id in checkpoint.completed_batch_ids:
            restored = tuple(candidate for candidate in checkpoint.candidates if candidate.batch_id == batch_id)
            outcome = next(outcome for outcome in checkpoint.outcomes if outcome.batch_id == batch_id)
            return checkpoint, replace(outcome, status=TranslationBatchStatus.RESTORED), restored
        batch_request = TranslationBatchRequest(
            batch_id,
            action,
            entries,
            request.run_spec,
            round_number,
            category,
            quest_id,
        )
        attempts = 0
        last_error: TranslationServiceError | None = None
        while attempts <= request.max_retries:
            if _cancelled(request.cancellation):
                return checkpoint, _cancelled_outcome(batch_request, attempts), ()
            attempts += 1
            try:
                response = self._llm.translate(batch_request, cancellation=request.cancellation)
                batch_candidates = _validate_response(
                    batch_request,
                    response,
                    action_by_key,
                    attempts,
                    request.owner_id,
                )
                outcome = TranslationBatchOutcome(
                    batch_id,
                    action,
                    TranslationBatchStatus.ACCEPTED,
                    tuple(entry.entry_key for entry in entries),
                    attempts,
                    "BATCH_ACCEPTED",
                    "The translation batch response was accepted.",
                    response_sha256=response.response_sha256,
                )
                accepted_checkpoint = checkpoint.accept_batch(outcome, batch_candidates)
                try:
                    request.checkpoint.save(accepted_checkpoint)
                except Exception:
                    return (
                        checkpoint,
                        TranslationBatchOutcome(
                            batch_id,
                            action,
                            TranslationBatchStatus.FAILED,
                            tuple(entry.entry_key for entry in entries),
                            attempts,
                            "TRANSLATION_CHECKPOINT_SAVE_FAILED",
                            "The accepted batch could not be recorded durably.",
                            retryable=True,
                            response_sha256=response.response_sha256,
                        ),
                        (),
                    )
                checkpoint = accepted_checkpoint
                if _cancelled(request.cancellation):
                    return checkpoint, outcome, batch_candidates
                return checkpoint, outcome, batch_candidates
            except TranslationServiceError as exc:
                last_error = exc
                if not exc.retryable or attempts > request.max_retries:
                    break
                if _wait_cancelled(request.cancellation, exc.retry_after or request.retry_backoff_seconds):
                    return checkpoint, _cancelled_outcome(batch_request, attempts), ()
            except Exception:
                last_error = TranslationServiceError(
                    "TRANSLATION_BATCH_INTERNAL",
                    "The translation batch failed unexpectedly.",
                )
                break
        error = last_error or TranslationServiceError(
            "TRANSLATION_BATCH_FAILED",
            "The translation batch failed.",
        )
        return (
            checkpoint,
            TranslationBatchOutcome(
                batch_id,
                action,
                TranslationBatchStatus.FAILED,
                tuple(entry.entry_key for entry in entries),
                attempts,
                error.code,
                error.safe_message,
                error.retryable,
                error.response_sha256,
            ),
            (),
        )


def _validate_response(
    request: TranslationBatchRequest,
    response: TranslationBatchResponse,
    action_by_key: dict,
    attempt: int,
    owner_id: str,
) -> tuple[CandidateTranslation, ...]:
    expected = {entry.entry_key: entry for entry in request.entries}
    values = dict(response.translations)
    if values.keys() != expected.keys():
        raise TranslationServiceError(
            "TRANSLATION_RESPONSE_KEYS_MISMATCH",
            "The translation response did not exactly cover the requested entries.",
            response_sha256=response.response_sha256,
        )
    diagnostic = Diagnostic(
        "TRANSLATION_CANDIDATE_ACCEPTED",
        "The candidate was accepted from a bounded LLM batch.",
        DiagnosticSeverity.INFO,
        details=(("attempt", attempt), ("operation", request.action.value)),
    )
    return tuple(
        CandidateTranslation(
            request.run_spec.run_id,
            key,
            expected[key].revision,
            action_by_key[key],
            text,
            request.batch_id,
            attempt,
            response.response_sha256,
            Provenance(
                request.run_spec.run_id,
                owner_id,
                "translation-llm-v2",
                metadata=(
                    ("batch_id", request.batch_id),
                    ("model", request.run_spec.model),
                    ("provider", request.run_spec.provider),
                    ("response_sha256", response.response_sha256),
                ),
            ),
            (diagnostic,),
        )
        for key, text in response.translations
    )


def _missing_polish_outcome(
    spec: TranslationRunSpec,
    entries: tuple[TranslationInput, ...],
    round_number: int,
    category: str,
    quest_id: str,
) -> TranslationBatchOutcome:
    batch_id = translation_batch_id(
        spec,
        TranslationAction.POLISH,
        entries,
        round_number=round_number,
        category=category,
        quest_id=f"{quest_id}:missing-source",
    )
    return TranslationBatchOutcome(
        batch_id,
        TranslationAction.POLISH,
        TranslationBatchStatus.FAILED,
        tuple(entry.entry_key for entry in entries),
        0,
        "POLISH_SOURCE_MISSING",
        "A polish action requires an existing or translated candidate text.",
    )


def _cancelled_outcome(request: TranslationBatchRequest, attempts: int) -> TranslationBatchOutcome:
    return TranslationBatchOutcome(
        request.batch_id,
        request.action,
        TranslationBatchStatus.CANCELLED,
        tuple(entry.entry_key for entry in request.entries),
        attempts,
        "TRANSLATION_CANCELLED",
        "The translation batch was cancelled before acceptance.",
    )


def _restored_final_keys(candidates: dict, outcomes: dict) -> set:
    actions = {outcome.batch_id: outcome.action for outcome in outcomes.values()}
    return {
        key
        for key, candidate in candidates.items()
        if candidate.action is not TranslationAction.BOTH or actions.get(candidate.batch_id) is TranslationAction.POLISH
    }


def _workload_result(
    request: TranslationWorkloadRequest,
    candidate_set: CandidateSet,
    *,
    cancelled: bool,
) -> OperationResult[dict]:
    success_keys = {candidate.entry_key for candidate in candidate_set.candidates}
    failed_keys = {
        key
        for outcome in candidate_set.batch_outcomes
        if outcome.status is TranslationBatchStatus.FAILED
        for key in outcome.entry_keys
        if key not in success_keys
    }
    actionable = {
        assignment.key
        for assignment in request.action_plan.assignments
        if assignment.action is not TranslationAction.SKIP
    }
    skipped = sum(assignment.action is TranslationAction.SKIP for assignment in request.action_plan.assignments)
    cancelled_keys = actionable.difference(success_keys, failed_keys) if cancelled else set()
    counts = OperationCounts(len(success_keys), len(failed_keys), skipped, len(cancelled_keys))
    diagnostics = tuple(
        Diagnostic(
            outcome.code,
            outcome.message,
            category=ErrorCategory.EXTERNAL,
            retryable=outcome.retryable,
            details=(
                ("batch_id", outcome.batch_id),
                ("entry_keys", tuple(key.serialize() for key in outcome.entry_keys)),
                ("attempts", outcome.attempts),
                ("response_sha256", outcome.response_sha256),
            ),
        )
        for outcome in candidate_set.batch_outcomes
        if outcome.status is TranslationBatchStatus.FAILED
    )
    if cancelled_keys:
        diagnostics += (
            Diagnostic(
                "TRANSLATION_CANCELLED",
                "The translation workload stopped accepting new batches after cancellation.",
                category=ErrorCategory.CANCELLED,
            ),
        )
    value = {"candidate_set": candidate_set.to_dict()}
    if counts.failed or counts.cancelled:
        if counts.succeeded:
            return OperationResult.partial(
                value,
                counts=counts,
                diagnostics=diagnostics,
                run_id=request.run_spec.run_id,
            )
        if counts.cancelled and not counts.failed:
            return OperationResult(
                OperationOutcome.CANCELLED,
                diagnostics=diagnostics,
                counts=counts,
                run_id=request.run_spec.run_id,
            )
        return OperationResult(
            OperationOutcome.FAILED,
            diagnostics=diagnostics,
            counts=counts,
            run_id=request.run_spec.run_id,
        )
    return OperationResult.completed(value, counts=counts, run_id=request.run_spec.run_id)


def _failed(run_id: str, code: str, message: str) -> OperationResult[dict]:
    return OperationResult(
        OperationOutcome.FAILED,
        diagnostics=(Diagnostic(code, message, category=ErrorCategory.CONFLICT),),
        counts=OperationCounts(failed=1),
        run_id=run_id,
    )


def _cancelled(cancellation: CancellationPort | None) -> bool:
    return cancellation is not None and cancellation.is_cancelled


def _wait_cancelled(cancellation: CancellationPort | None, seconds: float) -> bool:
    if cancellation is None:
        time.sleep(seconds)
        return False
    return cancellation.wait(seconds)
