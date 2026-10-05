"""Open proofreading followed by bounded deterministic terminology recovery."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import threading
from typing import Any

from ._open_proofread_stage import ProofreadStage as _OpenProofreadStage
from .postprocess import PostProcessCandidate, PostProcessStageOutcome
from .proofread_events import ProofreadEventLog
from .proofread_review import retain_syntax_questions
from .terminology_closure import ProofreadTerminologyClosure

TermResolver = Callable[[PostProcessCandidate], Mapping[object, object]]


class ProofreadStage:
    """Run broad proofreading, then conditionally repair remaining terminology."""

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
        refiner: object | None = None,
        refinement_batch_size: int = 5,
        checkpoint=None,
    ) -> None:
        # Kept for callers that inspect the configured routing client.
        self._llm_client = llm_client
        self._cancelled = threading.Event()
        self._terms_lock = threading.Lock()
        self._resolved_terms: dict[object, dict[str, str]] = {}
        self._default_max_workers = max_workers
        self._checkpoint = checkpoint
        self._checkpoint_session = None
        self._term_resolver = term_resolver or (lambda _: {})
        self._open_stage = _OpenProofreadStage(
            llm_client,
            term_resolver=term_resolver,
            target_locale=target_locale,
            game_profile=game_profile,
            polish_level=polish_level,
            model=model,
            max_tokens_per_batch=max_tokens_per_batch,
            max_items=max_items,
            max_output_tokens=max_output_tokens,
            max_workers=max_workers,
            term_observer=self._remember_terms,
        )
        if refiner is None and term_resolver is not None:
            from transbridge.ai_translator.post_processor.llm_refiner import LLMRefiner

            refiner = LLMRefiner(
                llm_client,
                game_profile=game_profile,
                target_lang=target_locale,
                max_output_tokens=max_output_tokens,
            )
        self._closure = ProofreadTerminologyClosure(
            refiner,
            model=model,
            max_tokens_per_batch=max_tokens_per_batch,
            max_items=refinement_batch_size,
        )

    def __call__(self, candidates: tuple[PostProcessCandidate, ...]) -> PostProcessStageOutcome:
        return self.run(candidates, max_workers=self._default_max_workers)

    def cancel(self) -> None:
        self._cancelled.set()
        self._open_stage.cancel()

    def run(
        self,
        candidates: tuple[PostProcessCandidate, ...],
        *,
        max_workers: int = 1,
        progress_callback: Callable[[int, int, str], None] | None = None,
        event_callback: Callable[[str], None] | None = None,
    ) -> PostProcessStageOutcome:
        events = ProofreadEventLog(event_callback)
        self._checkpoint_session = self._checkpoint.session(candidates) if self._checkpoint is not None else None
        restored = self._checkpoint_session.restore(self._term_resolver) if self._checkpoint_session is not None else {}
        pending = tuple(item for item in candidates if item.entry_key not in restored)
        if restored:
            events.emit(f"已恢复 {len(restored)} 条校对结果，剩余 {len(pending)} 条待处理")

        def on_progress(completed: int, total: int, message: str) -> None:
            completed, total = completed + len(restored), total + len(restored)
            events.progress("校对", completed, total)
            if progress_callback is not None:
                progress_callback(
                    completed, total, f"校对 {completed}/{total} 条（含已恢复结果）" if restored else message
                )

        with self._terms_lock:
            self._resolved_terms = dict(self._checkpoint_session.terms) if self._checkpoint_session is not None else {}
        batch_kwargs = {"batch_callback": self._checkpoint_session.save} if self._checkpoint_session is not None else {}
        if restored:
            on_progress(0, len(pending), f"已恢复 {len(restored)} 条校对结果")
        open_outcome = self._open_stage.run(
            pending,
            max_workers=max_workers,
            progress_callback=on_progress,
            event_callback=events.emit,
            **batch_kwargs,
        )
        all_open = {**restored, **{item.entry_key: item for item in open_outcome.candidates}}
        with self._terms_lock:
            resolved_terms = dict(self._resolved_terms)
        closed_candidates, closure_diagnostics = self._closure.apply(
            candidates,
            tuple(all_open[item.entry_key] for item in candidates),
            resolved_terms,
            progress_callback=progress_callback,
            is_cancelled=self._cancelled.is_set,
            max_workers=max_workers,
            event_callback=events.emit,
            **batch_kwargs,
        )
        closed_candidates, diagnostics = retain_syntax_questions(
            closed_candidates,
            (*open_outcome.diagnostics, *closure_diagnostics),
            cancelled=self._cancelled.is_set(),
        )
        return PostProcessStageOutcome(self.phase, closed_candidates, diagnostics)

    def _remember_terms(self, candidate: PostProcessCandidate, terms: Mapping[str, str]) -> None:
        if self._checkpoint_session is not None:
            self._checkpoint_session.observe(candidate, terms)
        with self._terms_lock:
            self._resolved_terms[candidate.entry_key] = dict(terms)


__all__ = ["ProofreadStage", "TermResolver"]
