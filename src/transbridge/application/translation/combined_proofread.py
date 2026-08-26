"""One-pass translation correction and polishing stage."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor, as_completed
import json
from typing import Any

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, ErrorCategory
from transbridge.application.io import EntryKey
from transbridge.infra.token_counting import TiktokenContentTokenCounter

from .postprocess import PostProcessCandidate, PostProcessStageOutcome
from .protected_syntax import protected_syntax_matches
from .token_batching import ContentBatch, StableContentBatcher

TermResolver = Callable[[PostProcessCandidate], Mapping[object, object]]
BatchResult = tuple[tuple[PostProcessCandidate, ...], tuple[Diagnostic, ...]]


class CombinedProofreadStage:
    """Correct and polish candidates with one LLM pass per content batch.

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
        self._batcher = StableContentBatcher(
            TiktokenContentTokenCounter(self._model),
            max_tokens_per_batch,
            max_items=max_items,
        )

    def __call__(self, candidates: tuple[PostProcessCandidate, ...]) -> PostProcessStageOutcome:
        return self.run(candidates, max_workers=self._max_workers)

    def cancel(self) -> None:
        """Forward cancellation to the configured run-scoped LLM client."""

        cancel = getattr(self._llm_client, "cancel", None)
        if callable(cancel):
            cancel()

    def run(
        self,
        candidates: tuple[PostProcessCandidate, ...],
        *,
        max_workers: int = 1,
        progress_callback: Callable[[int, int, str], None] | None = None,
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
        diagnostics = [
            Diagnostic(
                "PROOFREAD_CONTENT_TOKEN_LIMIT",
                item.message,
                category=ErrorCategory.INPUT,
                severity=DiagnosticSeverity.WARNING,
                details=(("entry_key", item.entry_key.to_dict()),),
            )
            for item in plan.oversized
        ]
        updated_by_key = {candidate.entry_key: candidate for candidate in candidates}
        for item in plan.oversized:
            updated_by_key[item.entry_key] = updated_by_key[item.entry_key].with_accepted(False)
        batch_results, progress_diagnostics = self._run_batches(
            plan.batches,
            max_workers=max_workers,
            completed_items=len(plan.oversized),
            total_items=len(candidates),
            progress_callback=progress_callback,
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

    def _run_batches(
        self,
        batches: tuple[ContentBatch[PostProcessCandidate], ...],
        *,
        max_workers: int,
        completed_items: int,
        total_items: int,
        progress_callback: Callable[[int, int, str], None] | None,
    ) -> tuple[dict[int, BatchResult], dict[int, Diagnostic]]:
        results: dict[int, BatchResult] = {}
        progress_diagnostics: dict[int, Diagnostic] = {}
        if max_workers == 1 or len(batches) <= 1:
            for batch in batches:
                try:
                    results[batch.index] = self._apply_batch(batch.items)
                except Exception as exc:  # defensive boundary around one independently recoverable batch
                    results[batch.index] = _failed_batch(batch.items, batch.index, exc)
                completed_items += len(batch.items)
                callback_diagnostic = _notify_progress(
                    progress_callback,
                    completed_items,
                    total_items,
                    batch.index,
                )
                if callback_diagnostic is not None:
                    progress_diagnostics[batch.index] = callback_diagnostic
            return results, progress_diagnostics

        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="proofread") as executor:
            futures: dict[Future[BatchResult], ContentBatch[PostProcessCandidate]] = {
                executor.submit(self._apply_batch, batch.items): batch for batch in batches
            }
            for future in as_completed(futures):
                batch = futures[future]
                try:
                    results[batch.index] = future.result()
                except Exception as exc:  # includes Future cancellation without losing its candidates
                    results[batch.index] = _failed_batch(batch.items, batch.index, exc)
                completed_items += len(batch.items)
                callback_diagnostic = _notify_progress(
                    progress_callback,
                    completed_items,
                    total_items,
                    batch.index,
                )
                if callback_diagnostic is not None:
                    progress_diagnostics[batch.index] = callback_diagnostic
        return results, progress_diagnostics

    def _apply_batch(
        self,
        candidates: tuple[PostProcessCandidate, ...],
    ) -> tuple[tuple[PostProcessCandidate, ...], tuple[Diagnostic, ...]]:
        def prepare_messages() -> list[dict[str, str]]:
            return self._messages(candidates)

        try:
            prepared_chat = getattr(self._llm_client, "chat_prepared", None)
            if callable(prepared_chat):
                response = prepared_chat(prepare_messages, self._max_output_tokens)
            else:
                response = self._llm_client.chat(prepare_messages(), self._max_output_tokens)
        except Exception as exc:
            return tuple(candidate.with_accepted(False) for candidate in candidates), (
                Diagnostic(
                    "PROOFREAD_LLM_CALL_FAILED",
                    "The combined proofread model call failed.",
                    category=ErrorCategory.EXTERNAL,
                    severity=DiagnosticSeverity.ERROR,
                    retryable=True,
                    details=(("error_type", type(exc).__name__),),
                ),
            )
        return self._apply_response(candidates, response)

    def _messages(self, candidates: tuple[PostProcessCandidate, ...]) -> list[dict[str, str]]:
        entries = []
        for candidate in candidates:
            resolved = self._term_resolver(candidate)
            if not isinstance(resolved, Mapping):
                raise TypeError("term_resolver must return a mapping")
            terms = {
                str(source): str(target)
                for source, target in resolved.items()
                if str(source).strip() and target is not None
            }
            entries.append({
                "entry_key": candidate.entry_key.to_dict(),
                "original": candidate.original,
                "current_translation": candidate.text,
                "context": candidate.context,
                "terms": terms,
            })
        output_contract = (
            'Return JSON only as {"results":[{"entry_key":{"namespace":"...","local_key":"..."},'
            '"final_translation":"..."}]}. Return every requested entry_key exactly once and no other keys.'
        )
        system = (
            f"You proofread translations for {self._game_profile} toward {self._target_locale}. "
            "Compare each original with its current translation. Correct translation errors and terminology, "
            f"then improve fluency and style. {_polish_level_instruction(self._polish_level)} "
            "If the current translation is already suitable, return it unchanged. "
            "Preserve placeholders and program tags exactly. Do not return reasons, confidence, or arbitration flags. "
            f"{output_contract}"
        )
        user = json.dumps({"entries": entries}, ensure_ascii=False, separators=(",", ":"))
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _apply_response(
        self,
        candidates: tuple[PostProcessCandidate, ...],
        response: object,
    ) -> tuple[tuple[PostProcessCandidate, ...], tuple[Diagnostic, ...]]:
        try:
            payload = json.loads(response) if isinstance(response, str) else None
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = None
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            return tuple(candidate.with_accepted(False) for candidate in candidates), (
                Diagnostic(
                    "PROOFREAD_RESPONSE_MALFORMED",
                    "The combined proofread response must be a JSON object with a results array.",
                    category=ErrorCategory.INPUT,
                    severity=DiagnosticSeverity.WARNING,
                ),
            )

        requested = {candidate.entry_key for candidate in candidates}
        values: dict[EntryKey, object] = {}
        duplicate_keys: set[EntryKey] = set()
        diagnostics: list[Diagnostic] = []
        for index, item in enumerate(payload["results"]):
            key = _result_key(item)
            if key is None:
                diagnostics.append(
                    Diagnostic(
                        "PROOFREAD_RESULT_ITEM_MALFORMED",
                        "A combined proofread result has an invalid entry_key.",
                        category=ErrorCategory.INPUT,
                        severity=DiagnosticSeverity.WARNING,
                        details=(("result_index", index),),
                    )
                )
                continue
            if key not in requested:
                diagnostics.append(
                    Diagnostic(
                        "PROOFREAD_RESPONSE_UNKNOWN_KEY",
                        "The combined proofread response contains an entry that was not requested.",
                        category=ErrorCategory.INPUT,
                        severity=DiagnosticSeverity.WARNING,
                        details=(("entry_key", key.to_dict()),),
                    )
                )
                continue
            if key in values:
                duplicate_keys.add(key)
                continue
            values[key] = item.get("final_translation")

        updated: list[PostProcessCandidate] = []
        for candidate in candidates:
            key = candidate.entry_key
            details = (("entry_key", key.to_dict()),)
            if key in duplicate_keys:
                diagnostics.append(
                    Diagnostic(
                        "PROOFREAD_RESPONSE_DUPLICATE_KEY",
                        "The combined proofread response returned an entry more than once.",
                        category=ErrorCategory.INPUT,
                        severity=DiagnosticSeverity.WARNING,
                        details=details,
                    )
                )
                updated.append(candidate.with_accepted(False))
                continue
            if key not in values:
                diagnostics.append(
                    Diagnostic(
                        "PROOFREAD_RESPONSE_MISSING_KEY",
                        "The combined proofread response omitted a requested entry.",
                        category=ErrorCategory.INPUT,
                        severity=DiagnosticSeverity.WARNING,
                        details=details,
                    )
                )
                updated.append(candidate.with_accepted(False))
                continue
            value = values[key]
            if not isinstance(value, str) or not value.strip():
                diagnostics.append(
                    Diagnostic(
                        "PROOFREAD_RESPONSE_EMPTY_TRANSLATION",
                        "The combined proofread response returned an empty or invalid translation.",
                        category=ErrorCategory.INPUT,
                        severity=DiagnosticSeverity.WARNING,
                        details=details,
                    )
                )
                updated.append(candidate.with_accepted(False))
                continue
            if not protected_syntax_matches(candidate.original, value):
                diagnostics.append(
                    Diagnostic(
                        "PROOFREAD_PROTECTED_SYNTAX_MISMATCH",
                        "The combined proofread result changed a placeholder or program tag.",
                        category=ErrorCategory.INPUT,
                        severity=DiagnosticSeverity.WARNING,
                        details=details,
                    )
                )
                updated.append(candidate.with_accepted(False))
                continue
            updated.append(candidate.with_text(value, self.phase))
        return tuple(updated), tuple(diagnostics)


def _result_key(item: object) -> EntryKey | None:
    if not isinstance(item, dict) or not isinstance(item.get("entry_key"), dict):
        return None
    try:
        return EntryKey.from_dict(item["entry_key"])
    except (KeyError, TypeError, ValueError):
        return None


def _validate_max_workers(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("max_workers must be a positive integer")


def _polish_level_instruction(level: str) -> str:
    return {
        "light": "Make only necessary corrections and otherwise preserve the current wording.",
        "moderate": "Balance necessary corrections with natural, restrained style improvements.",
        "aggressive": "Improve fluency and style more actively, without changing meaning or protected syntax.",
    }[level]


def _failed_batch(
    candidates: tuple[PostProcessCandidate, ...],
    batch_index: int,
    exc: Exception,
) -> tuple[tuple[PostProcessCandidate, ...], tuple[Diagnostic, ...]]:
    cancelled = isinstance(exc, CancelledError)
    return tuple(candidate.with_accepted(False) for candidate in candidates), (
        Diagnostic(
            "PROOFREAD_BATCH_CANCELLED" if cancelled else "PROOFREAD_BATCH_FAILED",
            "A combined proofread batch was cancelled."
            if cancelled
            else "A combined proofread batch failed unexpectedly.",
            category=ErrorCategory.CANCELLED if cancelled else ErrorCategory.INTERNAL,
            severity=DiagnosticSeverity.WARNING if cancelled else DiagnosticSeverity.ERROR,
            retryable=not cancelled,
            details=(("batch_index", batch_index), ("error_type", type(exc).__name__)),
        ),
    )


def _notify_progress(
    callback: Callable[[int, int, str], None] | None,
    completed: int,
    total: int,
    batch_index: int,
) -> Diagnostic | None:
    if callback is None:
        return None
    try:
        callback(completed, total, f"校对润色已完成 {completed}/{total} 条")
    except Exception as exc:
        return Diagnostic(
            "PROOFREAD_PROGRESS_CALLBACK_FAILED",
            "The combined proofread progress callback failed.",
            severity=DiagnosticSeverity.WARNING,
            details=(("batch_index", batch_index), ("error_type", type(exc).__name__)),
        )
    return None


__all__ = ["CombinedProofreadStage", "TermResolver"]
