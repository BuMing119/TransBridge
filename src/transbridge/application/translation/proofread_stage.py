"""Open proofreading followed by bounded deterministic terminology recovery."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import threading
from typing import Any

from ._open_proofread_stage import ProofreadStage as _OpenProofreadStage
from .postprocess import PostProcessCandidate, PostProcessStageOutcome
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
    ) -> None:
        # Kept for callers that inspect the configured routing client.
        self._llm_client = llm_client
        self._terms_lock = threading.Lock()
        self._resolved_terms: dict[object, dict[str, str]] = {}
        self._default_max_workers = max_workers
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
        self._open_stage.cancel()

    def run(
        self,
        candidates: tuple[PostProcessCandidate, ...],
        *,
        max_workers: int = 1,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> PostProcessStageOutcome:
        with self._terms_lock:
            self._resolved_terms.clear()
        open_outcome = self._open_stage.run(
            candidates,
            max_workers=max_workers,
            progress_callback=progress_callback,
        )
        with self._terms_lock:
            resolved_terms = dict(self._resolved_terms)
        closed_candidates, closure_diagnostics = self._closure.apply(
            candidates,
            open_outcome.candidates,
            resolved_terms,
        )
        return PostProcessStageOutcome(
            self.phase,
            closed_candidates,
            (*open_outcome.diagnostics, *closure_diagnostics),
        )

    def _remember_terms(self, candidate: PostProcessCandidate, terms: Mapping[str, str]) -> None:
        with self._terms_lock:
            self._resolved_terms[candidate.entry_key] = dict(terms)


__all__ = ["ProofreadStage", "TermResolver"]
