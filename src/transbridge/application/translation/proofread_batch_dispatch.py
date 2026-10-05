"""Bounded first-pass proofreading dispatch and progress accounting."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import threading

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, ErrorCategory

from .ai_request_budget import AiRequestCancelledError
from .postprocess import PostProcessCandidate
from .token_batching import ContentBatch

BatchResult = tuple[tuple[PostProcessCandidate, ...], tuple[Diagnostic, ...]]


def run_proofread_batches(
    batches: tuple[ContentBatch[PostProcessCandidate], ...],
    apply_batch: Callable[[tuple[PostProcessCandidate, ...]], BatchResult],
    cancelled: threading.Event,
    *,
    max_workers: int,
    completed_items: int,
    total_items: int,
    progress_callback: Callable[[int, int, str], None] | None,
    batch_callback: Callable[[tuple[PostProcessCandidate, ...]], None] | None = None,
) -> tuple[dict[int, BatchResult], dict[int, Diagnostic]]:
    results: dict[int, BatchResult] = {}
    progress_diagnostics: dict[int, Diagnostic] = {}
    pending_batches = iter(batches)
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="proofread") as executor:
        pending = {}

        def fill():
            while len(pending) < max_workers and not cancelled.is_set():
                batch = next(pending_batches, None)
                if batch is None:
                    break
                pending[executor.submit(apply_batch, batch.items)] = batch

        fill()
        while pending:
            ready, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in ready:
                batch = pending.pop(future)
                try:
                    results[batch.index] = future.result()
                except Exception as exc:
                    results[batch.index] = _failed_batch(batch, exc)
                if batch_callback is not None:
                    # Durability failures must fail the run, not become successful progress.
                    batch_callback(results[batch.index][0])
                completed_items += len(batch.items)
                if progress_callback is not None:
                    try:
                        progress_callback(completed_items, total_items, f"校对 {completed_items}/{total_items} 条")
                    except Exception as exc:
                        progress_diagnostics[batch.index] = Diagnostic(
                            "PROOFREAD_PROGRESS_CALLBACK_FAILED",
                            "The Proofread progress callback failed.",
                            severity=DiagnosticSeverity.WARNING,
                            details=(("batch_index", batch.index), ("error_type", type(exc).__name__)),
                        )
            fill()

    skipped = tuple(pending_batches)
    for batch in skipped:
        results[batch.index] = (tuple(item.with_accepted(False) for item in batch.items), ())
    if skipped:
        first = skipped[0].index
        progress_diagnostics[first] = Diagnostic(
            "PROOFREAD_BATCH_NOT_STARTED",
            "Proofreading stopped before these entries were started.",
            category=ErrorCategory.CANCELLED,
            severity=DiagnosticSeverity.WARNING,
            details=(
                ("reason", "not_started"),
                ("entry_keys", tuple(item.entry_key.to_dict() for batch in skipped for item in batch.items)),
            ),
        )
    _merge_cancelled_calls(results)
    return results, progress_diagnostics


def _merge_cancelled_calls(results: dict[int, BatchResult]) -> None:
    cancelled_keys = []
    first_index = None
    for index, (items, diagnostics) in results.items():
        retained = []
        for diagnostic in diagnostics:
            if diagnostic.code == "PROOFREAD_LLM_CALL_CANCELLED":
                if first_index is None:
                    first_index = index
                cancelled_keys.extend(dict(diagnostic.details).get("entry_keys", ()))
            else:
                retained.append(diagnostic)
        results[index] = items, tuple(retained)
    if first_index is not None:
        items, diagnostics = results[first_index]
        results[first_index] = (
            items,
            (
                *diagnostics,
                Diagnostic(
                    "PROOFREAD_LLM_CALL_CANCELLED",
                    "Proofreading calls were cancelled.",
                    category=ErrorCategory.CANCELLED,
                    severity=DiagnosticSeverity.WARNING,
                    details=(("entry_keys", tuple(cancelled_keys)),),
                ),
            ),
        )


def _failed_batch(batch: ContentBatch[PostProcessCandidate], exc: Exception) -> BatchResult:
    cancelled = isinstance(exc, AiRequestCancelledError)
    return tuple(candidate.with_accepted(False) for candidate in batch.items), (
        Diagnostic(
            "PROOFREAD_LLM_CALL_CANCELLED" if cancelled else "PROOFREAD_BATCH_FAILED",
            "A Proofread batch was cancelled." if cancelled else "A Proofread batch failed unexpectedly.",
            category=ErrorCategory.CANCELLED if cancelled else ErrorCategory.INTERNAL,
            severity=DiagnosticSeverity.WARNING if cancelled else DiagnosticSeverity.ERROR,
            retryable=not cancelled,
            details=(
                ("batch_index", batch.index),
                ("error_type", type(exc).__name__),
                ("entry_keys", tuple(candidate.entry_key.to_dict() for candidate in batch.items)),
            ),
        ),
    )
