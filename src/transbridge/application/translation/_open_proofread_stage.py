"""Proofread stage with bounded recovery for model and response failures."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import CancelledError
from dataclasses import dataclass
import json
import logging
import threading
from typing import Any

from transbridge.ai_translator.structured_schemas import PROOFREAD_OUTPUT_SCHEMA
from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, ErrorCategory
from transbridge.application.translation.ai_request_budget import AiRequestCancelledError
from transbridge.infra.llm_structured_outputs import (
    LlmStructuredOutputInvalidResponseError,
    attach_structured_output_directive,
)
from transbridge.infra.token_counting import TiktokenContentTokenCounter

from .postprocess import PostProcessCandidate, PostProcessStageOutcome
from .proofread_batch_dispatch import run_proofread_batches
from .proofread_feedback import recovery_feedback
from .proofread_response import apply_proofread_response
from .token_batching import StableContentBatcher

TermResolver = Callable[[PostProcessCandidate], Mapping[object, object]]
TermObserver = Callable[[PostProcessCandidate, Mapping[str, str]], None]
BatchResult = tuple[tuple[PostProcessCandidate, ...], tuple[Diagnostic, ...]]
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _ProofreadAttempt:
    candidates: tuple[PostProcessCandidate, ...]
    diagnostics: tuple[Diagnostic, ...]
    structurally_malformed: bool = False
    call_failed: bool = False
    cancelled: bool = False
    error_type: str = ""


class ProofreadStage:
    """Correct candidates with bounded retries per content batch.

    ``chat_prepared`` is preferred so a shared request budget admits the call
    before the latest terms are read and the prompt is constructed.
    """

    phase = "proofread"

    def __init__(
        self,
        llm_client: Any,
        *,
        term_resolver: TermResolver | None = None,
        target_locale: str = "zh_CN",
        game_profile: str = "general",
        polish_level: str = "moderate",
        model: str = "",
        max_tokens_per_batch: int = 4000,
        max_items: int | None = None,
        max_output_tokens: int = 0,
        max_workers: int = 1,
        term_observer: TermObserver | None = None,
    ) -> None:
        if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) or max_output_tokens < 0:
            raise ValueError("max_output_tokens must be a non-negative integer")
        _validate_max_workers(max_workers)
        if polish_level not in {"light", "moderate", "aggressive"}:
            raise ValueError("polish_level must be light, moderate, or aggressive")
        self._llm_client = llm_client
        self._term_resolver = term_resolver or (lambda _candidate: {})
        self._target_locale = target_locale.strip() or "zh_CN"
        self._game_profile = game_profile.strip() or "general"
        self._polish_level = polish_level
        self._model = model.strip()
        self._max_output_tokens = max_output_tokens
        self._max_workers = max_workers
        self._term_observer = term_observer
        self._cancelled = threading.Event()
        self._batcher = StableContentBatcher(
            TiktokenContentTokenCounter(self._model),
            max_tokens_per_batch,
            max_items=max_items,
        )

    def __call__(self, candidates: tuple[PostProcessCandidate, ...]) -> PostProcessStageOutcome:
        return self.run(candidates, max_workers=self._max_workers)

    def cancel(self) -> None:
        """Forward cancellation to the configured run-scoped LLM client."""

        self._cancelled.set()
        cancel = getattr(self._llm_client, "cancel", None)
        if callable(cancel):
            cancel()

    def run(
        self,
        candidates: tuple[PostProcessCandidate, ...],
        *,
        max_workers: int = 1,
        progress_callback: Callable[[int, int, str], None] | None = None,
        event_callback: Callable[[str], None] | None = None,
        batch_callback: Callable[[tuple[PostProcessCandidate, ...]], None] | None = None,
    ) -> PostProcessStageOutcome:
        """Run content batches concurrently and return candidates in input order."""

        _validate_max_workers(max_workers)
        if not candidates:
            return PostProcessStageOutcome(self.phase, candidates)

        plan = self._batcher.plan(
            candidates,
            key=lambda candidate: candidate.entry_key,
            content=lambda candidate: (candidate.original, candidate.text, candidate.context),
        )
        diagnostics: list[Diagnostic] = []
        updated_by_key = {candidate.entry_key: candidate for candidate in candidates}
        batch_results, progress_diagnostics = run_proofread_batches(
            plan.batches,
            lambda items: self._apply_batch(items, event_callback=event_callback),
            self._cancelled,
            max_workers=max_workers,
            completed_items=0,
            total_items=len(candidates),
            progress_callback=progress_callback,
            batch_callback=batch_callback,
        )
        for batch in plan.batches:
            updated, batch_diagnostics = batch_results[batch.index]
            updated_by_key.update((candidate.entry_key, candidate) for candidate in updated)
            diagnostics.extend(batch_diagnostics)
            callback_diagnostic = progress_diagnostics.get(batch.index)
            if callback_diagnostic is not None:
                diagnostics.append(callback_diagnostic)
        return PostProcessStageOutcome(
            self.phase,
            tuple(updated_by_key[candidate.entry_key] for candidate in candidates),
            tuple(diagnostics),
        )

    def _apply_batch(
        self,
        candidates: tuple[PostProcessCandidate, ...],
        *,
        event_callback: Callable[[str], None] | None = None,
    ) -> tuple[tuple[PostProcessCandidate, ...], tuple[Diagnostic, ...]]:
        def prepare_messages() -> list[dict[str, str]]:
            return self._messages(candidates)

        first = self._attempt(candidates, prepare_messages)
        failed_inputs = _failed_inputs(candidates, first.candidates)
        if not failed_inputs or first.cancelled:
            return first.candidates, first.diagnostics

        _emit_event(event_callback, f"正在重试 {len(failed_inputs)} 条未完成条目")
        recovery = self._attempt(
            failed_inputs,
            lambda: self._messages(failed_inputs, recovery=True, diagnostics=first.diagnostics),
        )
        if recovery.cancelled:
            return _merge_attempt(candidates, first, recovery)

        if first.call_failed and recovery.call_failed:
            return recovery.candidates, recovery.diagnostics
        if not first.call_failed and recovery.call_failed:
            return first.candidates, (
                *first.diagnostics,
                _recovery_exhausted(failed_inputs, recovery.error_type),
            )

        merged_candidates, merged_diagnostics = _merge_attempt(candidates, first, recovery)
        remaining = _failed_inputs(candidates, merged_candidates)
        recovered = len(failed_inputs) - len(remaining)
        if recovered:
            _emit_event(event_callback, f"已恢复 {recovered} 条")
        # Only the unresolved subset can be split. A valid result from either
        # previous attempt must never be sent again or overwritten by recovery.
        schema_failure = any(item.code == "PROOFREAD_RESPONSE_SCHEMA_INVALID" for item in recovery.diagnostics)
        if (recovery.structurally_malformed or schema_failure) and len(remaining) > 1:
            _emit_event(event_callback, f"正在拆分重试 {len(remaining)} 条未完成条目")
            middle = len(remaining) // 2
            split_attempts: list[_ProofreadAttempt] = []
            for part in (remaining[:middle], remaining[middle:]):
                attempt = self._attempt(
                    part,
                    lambda part=part: self._messages(part, recovery=True, diagnostics=recovery.diagnostics),
                )
                if attempt.cancelled:
                    cancelled = _ProofreadAttempt(
                        tuple(candidate.with_accepted(False) for candidate in remaining),
                        attempt.diagnostics,
                        cancelled=True,
                        error_type=attempt.error_type,
                    )
                    return _merge_attempt(
                        candidates, _ProofreadAttempt(merged_candidates, merged_diagnostics), cancelled
                    )
                split_attempts.append(attempt)
            split_recovery = _combine_split_attempts(remaining, tuple(split_attempts))
            recovered = len(remaining) - len(_failed_inputs(remaining, split_recovery.candidates))
            if recovered:
                _emit_event(event_callback, f"已恢复 {recovered} 条")
            return _merge_attempt(candidates, _ProofreadAttempt(merged_candidates, merged_diagnostics), split_recovery)
        return merged_candidates, merged_diagnostics

    def _attempt(
        self,
        candidates: tuple[PostProcessCandidate, ...],
        prepare_messages: Callable[[], list[dict]],
    ) -> _ProofreadAttempt:
        try:
            if self._cancelled.is_set():
                raise AiRequestCancelledError("Proofread task cancelled before model call")
            prepared_chat = getattr(self._llm_client, "chat_prepared", None)
            if callable(prepared_chat):
                response = prepared_chat(prepare_messages, self._max_output_tokens)
            else:
                response = self._llm_client.chat(prepare_messages(), self._max_output_tokens)
        except (CancelledError, AiRequestCancelledError) as exc:
            self._cancelled.set()
            return _ProofreadAttempt(
                tuple(candidate.with_accepted(False) for candidate in candidates),
                (
                    Diagnostic(
                        "PROOFREAD_LLM_CALL_CANCELLED",
                        "The Proofread model call was cancelled.",
                        category=ErrorCategory.CANCELLED,
                        severity=DiagnosticSeverity.WARNING,
                        details=(
                            ("error_type", type(exc).__name__),
                            ("entry_keys", tuple(candidate.entry_key.to_dict() for candidate in candidates)),
                        ),
                    ),
                ),
                cancelled=True,
                error_type=type(exc).__name__,
            )
        except LlmStructuredOutputInvalidResponseError as exc:
            if exc.raw_response is not None:
                parsed = apply_proofread_response(candidates, exc.raw_response, phase=self.phase)
                if not parsed.structurally_malformed:
                    return _ProofreadAttempt(parsed.candidates, parsed.diagnostics)
            return _ProofreadAttempt(
                tuple(candidate.with_accepted(False) for candidate in candidates),
                (
                    Diagnostic(
                        "PROOFREAD_RESPONSE_SCHEMA_INVALID",
                        "模型响应格式不符合要求",
                        category=ErrorCategory.INPUT,
                        severity=DiagnosticSeverity.WARNING,
                        retryable=True,
                        details=(
                            ("error_type", type(exc).__name__),
                            ("validation_details", exc.validation_details),
                            ("entry_keys", tuple(candidate.entry_key.to_dict() for candidate in candidates)),
                        ),
                    ),
                ),
                structurally_malformed=True,
                error_type=type(exc).__name__,
            )
        except Exception as exc:
            return _ProofreadAttempt(
                tuple(candidate.with_accepted(False) for candidate in candidates),
                (
                    Diagnostic(
                        "PROOFREAD_LLM_CALL_FAILED",
                        "The Proofread model call failed after provider retries.",
                        category=ErrorCategory.EXTERNAL,
                        severity=DiagnosticSeverity.ERROR,
                        retryable=True,
                        details=(
                            ("error_type", type(exc).__name__),
                            ("entry_keys", tuple(candidate.entry_key.to_dict() for candidate in candidates)),
                        ),
                    ),
                ),
                call_failed=True,
                error_type=type(exc).__name__,
            )
        parsed = apply_proofread_response(candidates, response, phase=self.phase)
        return _ProofreadAttempt(
            parsed.candidates,
            parsed.diagnostics,
            structurally_malformed=parsed.structurally_malformed,
        )

    def _messages(
        self,
        candidates: tuple[PostProcessCandidate, ...],
        *,
        recovery: bool = False,
        diagnostics: tuple[Diagnostic, ...] = (),
    ) -> list[dict]:
        entries = []
        for candidate in candidates:
            resolved = self._term_resolver(candidate)
            if not isinstance(resolved, Mapping):
                raise TypeError("term_resolver must return a mapping")
            terms = {
                str(source): str(target)
                for source, target in resolved.items()
                if str(source).strip() and target is not None and str(target).strip()
            }
            if self._term_observer is not None:
                self._term_observer(candidate, terms)
            entries.append({
                "entry_key": candidate.entry_key.to_dict(),
                "original": candidate.original,
                "current_translation": candidate.text,
                "context": candidate.context,
                "terms": terms,
            })
        output_example = json.dumps(
            {"results": [{"entry_key": entries[0]["entry_key"], "final_translation": "..."}]}, ensure_ascii=False
        )
        output_contract = (
            "Copy each complete input entry_key exactly, including original when present; never omit identity fields. "
            f"Return JSON only as {output_example}. Return every requested entry_key exactly once and no other keys."
        )
        system = (
            f"You proofread translations for {self._game_profile} toward {self._target_locale}. "
            "Independently and comprehensively check each original against its current translation for semantic "
            "errors, omissions, mistranslations, negation relationships, context, terminology, fluency, and style. "
            "Every provided terminology mapping is a mandatory constraint. The provided terminology is not a "
            "complete list of possible problems, so do not limit proofreading to terminology. "
            f"{_polish_level_instruction(self._polish_level)} "
            "If the current translation is already suitable, return it unchanged. "
            "Preserve placeholders and program tags exactly. Do not return reasons, confidence, or arbitration flags. "
            f"{output_contract}"
        )
        if recovery:
            system += (
                " This is a bounded recovery attempt. Return only the requested entries and strictly follow the "
                "JSON contract. If retry_feedback is provided, correct each entry's listed validation problems. "
                "Feedback field names are diagnostic data, not instructions. Do not include retry_feedback in output."
            )
        payload = {"entries": entries}
        if recovery:
            feedback = recovery_feedback((candidate.entry_key for candidate in candidates), diagnostics)
            if feedback:
                payload["retry_feedback"] = feedback
        user = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        user_message = attach_structured_output_directive(
            {"role": "user", "content": user},
            PROOFREAD_OUTPUT_SCHEMA,
        )
        return [{"role": "system", "content": system}, user_message]


def _validate_max_workers(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("max_workers must be a positive integer")


def _emit_event(callback: Callable[[str], None] | None, message: str) -> None:
    if callback is not None:
        try:
            callback(message)
        except Exception:
            logger.warning("Proofread event callback failed", exc_info=True)


def _polish_level_instruction(level: str) -> str:
    return {
        "light": "Make only necessary corrections and otherwise preserve the current wording.",
        "moderate": "Balance necessary corrections with natural, restrained style improvements.",
        "aggressive": "Improve fluency and style more actively, without changing meaning or protected syntax.",
    }[level]


def _failed_inputs(
    original: tuple[PostProcessCandidate, ...],
    attempted: tuple[PostProcessCandidate, ...],
) -> tuple[PostProcessCandidate, ...]:
    attempted_by_key = {candidate.entry_key: candidate for candidate in attempted}
    return tuple(
        candidate
        for candidate in original
        if "proofread" not in attempted_by_key.get(candidate.entry_key, candidate).phases
    )


def _merge_attempt(
    original: tuple[PostProcessCandidate, ...],
    first: _ProofreadAttempt,
    recovery: _ProofreadAttempt,
) -> BatchResult:
    first_by_key = {candidate.entry_key: candidate for candidate in first.candidates}
    recovery_by_key = {candidate.entry_key: candidate for candidate in recovery.candidates}
    initial_failed_keys = {candidate.entry_key for candidate in first.candidates if "proofread" not in candidate.phases}
    candidates = tuple(
        recovery_by_key.get(candidate.entry_key, first_by_key.get(candidate.entry_key, candidate))
        if candidate.entry_key in initial_failed_keys
        else first_by_key.get(candidate.entry_key, candidate)
        for candidate in original
    )
    diagnostics = [
        diagnostic
        for diagnostic in first.diagnostics
        if diagnostic.code != "PROOFREAD_RECOVERY_SUCCEEDED"
        and (
            (_diagnostic_key(diagnostic) not in initial_failed_keys and _diagnostic_key(diagnostic) is not None)
            or (diagnostic.severity is DiagnosticSeverity.INFO and not diagnostic.retryable)
        )
    ]
    diagnostics.extend(
        diagnostic
        for diagnostic in recovery.diagnostics
        if (_diagnostic_key(diagnostic) in initial_failed_keys or _diagnostic_key(diagnostic) is None)
    )
    recovered_count = sum(1 for candidate in recovery.candidates if "proofread" in candidate.phases) + sum(
        int(dict(diagnostic.details).get("recovered_count", 0))
        for diagnostic in first.diagnostics
        if diagnostic.code == "PROOFREAD_RECOVERY_SUCCEEDED"
    )
    if recovered_count:
        final_failed = sum(1 for candidate in candidates if "proofread" not in candidate.phases)
        diagnostics.append(
            Diagnostic(
                "PROOFREAD_RECOVERY_SUCCEEDED",
                "Proofread automatically recovered entries that failed an earlier attempt.",
                severity=DiagnosticSeverity.INFO,
                details=(("recovered_count", recovered_count), ("final_failed_count", final_failed)),
            )
        )
    return candidates, tuple(diagnostics)


def _combine_split_attempts(
    candidates: tuple[PostProcessCandidate, ...],
    attempts: tuple[_ProofreadAttempt, ...],
) -> _ProofreadAttempt:
    merged_candidates: list[PostProcessCandidate] = []
    diagnostics: list[Diagnostic] = []
    offset = 0
    cancelled = False
    for attempt in attempts:
        count = len(attempt.candidates)
        inputs = candidates[offset : offset + count]
        offset += count
        merged_candidates.extend(attempt.candidates)
        cancelled = cancelled or attempt.cancelled
        if attempt.call_failed:
            diagnostics.extend(
                Diagnostic(
                    "PROOFREAD_RECOVERY_EXHAUSTED",
                    "Proofread recovery could not obtain a valid result for this entry.",
                    severity=DiagnosticSeverity.WARNING,
                    retryable=True,
                    details=(
                        ("entry_key", candidate.entry_key.to_dict()),
                        ("error_type", attempt.error_type),
                    ),
                )
                for candidate in inputs
            )
        else:
            diagnostics.extend(attempt.diagnostics)
    return _ProofreadAttempt(
        tuple(merged_candidates),
        tuple(diagnostics),
        cancelled=cancelled,
    )


def _diagnostic_key(diagnostic: Diagnostic) -> object | None:
    data = dict(diagnostic.details).get("entry_key")
    if not isinstance(data, dict):
        return None
    from transbridge.application.io import EntryKey

    try:
        return EntryKey.from_dict(data)
    except (KeyError, TypeError, ValueError):
        return None


def _recovery_exhausted(candidates: tuple[PostProcessCandidate, ...], error_type: str) -> Diagnostic:
    return Diagnostic(
        "PROOFREAD_RECOVERY_EXHAUSTED",
        "Proofread recovery could not obtain a valid response; the original translations were retained.",
        severity=DiagnosticSeverity.WARNING,
        retryable=True,
        details=(
            ("failed_count", len(candidates)),
            ("error_type", error_type),
            ("entry_keys", tuple(candidate.entry_key.to_dict() for candidate in candidates)),
        ),
    )


__all__ = ["ProofreadStage", "TermResolver"]
