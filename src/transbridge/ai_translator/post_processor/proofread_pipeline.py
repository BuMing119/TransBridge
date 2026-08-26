"""Candidate-only proofreading pipeline for standalone and mixed polish runs."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
import threading

from transbridge.application.translation.ai_execution_profile import AiExecutionProfile

from .base import PostProcessIssue, PostProcessResult
from .post_processor import PostProcessor, PostProcessorConfig


@dataclass(frozen=True, slots=True)
class ProofreadResult:
    """Compatibility projection plus traceability for one final candidate."""

    entry_id: str
    entry_key: str
    original_translation: str
    polished_translation: str
    confidence: float
    needs_arbitration: bool
    note: str
    verdict: str
    issues: tuple[PostProcessIssue, ...] = ()
    refined_translation: str | None = None
    changes: tuple[dict, ...] = field(default_factory=tuple)

    @property
    def accepted(self) -> bool:
        return self.verdict == "pass" and self.confidence > 0 and bool(self.polished_translation)


class ProofreadPipeline:
    """Runs existing post-process stages on copies and returns commit candidates."""

    def __init__(self, processor: PostProcessor, profile: AiExecutionProfile) -> None:
        self._processor = processor
        self.profile = profile

    @classmethod
    def create(
        cls,
        *,
        profile: AiExecutionProfile,
        llm_client: object,
        term_manager: object | None = None,
    ) -> ProofreadPipeline:
        config = PostProcessorConfig(
            game_profile=profile.game_profile,
            target_lang=profile.target_lang,
            enable_consistency_check=profile.enable_consistency_check,
            enable_format_validation=profile.enable_format_validation,
            enable_quality_gate=profile.enable_quality_gate,
            quality_gate_batch_size=profile.quality_gate_batch_size,
            enable_refinement=profile.enable_refinement,
            refinement_batch_size=profile.refinement_batch_size,
            enable_polish=profile.enable_polish,
            polish_scope=profile.polish_scope,
            polish_level=profile.polish_level,
            polish_batch_size=profile.polish_batch_size,
            enable_llm_arbitration=profile.enable_arbitration,
            strict_arbitration=profile.strict_arbitration,
            arbitration_batch_size=profile.arbitration_batch_size,
        )
        processor = PostProcessor(config)
        processor.register_default_checkers(term_manager=term_manager, llm_client=llm_client)
        return cls(processor, profile)

    def process(
        self,
        entries: Iterable[object],
        *,
        progress_callback: Callable[[str, int, int, str], None] | None = None,
        stop_event: threading.Event | None = None,
        pause_event: threading.Event | None = None,
        max_workers: int = 1,
    ) -> dict[str, ProofreadResult]:
        originals = tuple(entries)
        working = [replace(entry) for entry in originals]
        result = self._processor.process_entries(
            working,
            progress_callback=progress_callback,
            stop_event=stop_event,
            pause_event=pause_event,
            max_workers=max_workers,
            apply_changes=False,
        )
        return self._project(originals, result)

    def _project(self, entries: tuple[object, ...], result: PostProcessResult) -> dict[str, ProofreadResult]:
        issues_by_id: dict[str, list[PostProcessIssue]] = {}
        for issue in result.issues:
            issues_by_id.setdefault(str(issue.entry_id), []).append(issue)
        refine_results = result.refine_results or {}
        polish_results = result.polish_results or {}
        decisions = result.decisions or {}
        projected: dict[str, ProofreadResult] = {}
        for entry in entries:
            entry_id = str(entry.id)
            refined = refine_results.get(entry_id)
            polished = polish_results.get(entry_id)
            decision = decisions.get(entry_id)
            final_translation = (
                getattr(polished, "polished_translation", "")
                or getattr(refined, "refined_translation", "")
                or entry.translation
                or ""
            )
            confidence_value = getattr(decision, "confidence", None)
            if decision is None and self.profile.enable_arbitration:
                confidence_value = 0.0
            if confidence_value is None:
                confidence_value = getattr(polished, "confidence", None)
            if confidence_value is None:
                confidence_value = getattr(refined, "confidence", None)
            if confidence_value is None:
                confidence_value = 0.0 if self.profile.enable_arbitration or issues_by_id.get(entry_id) else 1.0
            confidence = float(confidence_value)
            default_verdict = "pending" if self.profile.enable_arbitration or issues_by_id.get(entry_id) else "pass"
            verdict = str(getattr(decision, "verdict", default_verdict))
            notes = [
                str(value)
                for value in (
                    getattr(refined, "note", ""),
                    getattr(polished, "note", ""),
                    getattr(decision, "reason", ""),
                )
                if value
            ]
            projected[entry_id] = ProofreadResult(
                entry_id=entry_id,
                entry_key=str(entry.key),
                original_translation=entry.translation or "",
                polished_translation=final_translation,
                confidence=confidence,
                needs_arbitration=verdict != "pass",
                note="；".join(notes),
                verdict=verdict,
                issues=tuple(issues_by_id.get(entry_id, ())),
                refined_translation=getattr(refined, "refined_translation", None),
                changes=tuple(getattr(polished, "changes", ()) or ()),
            )
        return projected


__all__ = ["ProofreadPipeline", "ProofreadResult"]
