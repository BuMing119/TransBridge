"""Candidate-only proofreading pipeline for standalone and mixed polish runs."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
import threading

from transbridge.application.translation.ai_execution_profile import AiExecutionProfile
from transbridge.application.translation.postprocess import PostProcessCandidate
from transbridge.application.translation.proofread_stage import ProofreadStage

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

    def __init__(
        self,
        processor: PostProcessor,
        profile: AiExecutionProfile,
        *,
        proofread_stage: ProofreadStage | None = None,
    ) -> None:
        self._processor = processor
        self.profile = profile
        self._proofread_stage = proofread_stage

    @classmethod
    def create(
        cls,
        *,
        profile: AiExecutionProfile,
        llm_client: object,
        arbitration_llm_client: object | None = None,
        term_manager: object | None = None,
        model: str = "",
        max_tokens_per_batch: int = 2000,
        max_output_tokens: int = 0,
        token_counter: object | None = None,
    ) -> ProofreadPipeline:
        config = PostProcessorConfig(
            game_profile=profile.game_profile,
            target_lang=profile.target_lang,
            model=model,
            max_tokens_per_batch=max_tokens_per_batch,
            max_output_tokens=max_output_tokens,
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
        processor = PostProcessor(config, token_counter=token_counter)
        processor.register_default_checkers(
            term_manager=term_manager,
            llm_client=llm_client,
            arbitration_llm_client=arbitration_llm_client,
        )
        proofread_stage = None
        if profile.enable_proofread and llm_client is not None:

            def resolve_terms(candidate: PostProcessCandidate) -> dict:
                if term_manager is None or not candidate.original:
                    return {}
                try:
                    contextual = getattr(term_manager, "match_terms_for_entry", None)
                    if callable(contextual):
                        return dict(contextual(candidate))
                    lookup_context = getattr(term_manager, "lookup_context_for_entry", None)
                    match_terms = getattr(term_manager, "match_terms", None)
                    if callable(lookup_context) and callable(match_terms):
                        return dict(match_terms([candidate.original], context=lookup_context(candidate)))
                    return {}
                except Exception:
                    return {}

            proofread_stage = ProofreadStage(
                llm_client,
                term_resolver=resolve_terms,
                target_locale=profile.target_lang,
                game_profile=profile.game_profile,
                polish_level=profile.polish_level,
                model=model,
                max_tokens_per_batch=max_tokens_per_batch,
                refinement_batch_size=profile.refinement_batch_size,
                max_output_tokens=max_output_tokens,
            )
        return cls(processor, profile, proofread_stage=proofread_stage)

    def process(
        self,
        entries: Iterable[object],
        *,
        progress_callback: Callable[[str, int, int, str], None] | None = None,
        log_callback: Callable[[str], None] | None = None,
        stop_event: threading.Event | None = None,
        pause_event: threading.Event | None = None,
        max_workers: int = 1,
    ) -> dict[str, ProofreadResult]:
        originals = tuple(entries)
        if self.profile.enable_proofread:
            return self._process_proofread(
                originals,
                progress_callback=progress_callback,
                log_callback=log_callback,
                stop_event=stop_event,
                pause_event=pause_event,
                max_workers=max_workers,
            )
        working = [replace(entry) for entry in originals]
        result = self._processor.process_entries(
            working,
            progress_callback=progress_callback,
            log_callback=log_callback,
            stop_event=stop_event,
            pause_event=pause_event,
            max_workers=max_workers,
            apply_changes=False,
        )
        return self._project(originals, result)

    def _process_proofread(
        self,
        entries: tuple[object, ...],
        *,
        progress_callback: Callable[[str, int, int, str], None] | None,
        log_callback: Callable[[str], None] | None,
        stop_event: threading.Event | None,
        pause_event: threading.Event | None,
        max_workers: int,
    ) -> dict[str, ProofreadResult]:
        total = len(entries)
        if progress_callback:
            progress_callback("proofread", 0, total, f"开始校对 {total} 个条目...")
        if self._proofread_stage is None:
            return {
                str(entry.id): self._proofread_result(entry, valid=False, note="未配置可用的校对模型")
                for entry in entries
            }
        while pause_event is not None and not pause_event.is_set():
            if stop_event is not None and stop_event.is_set():
                return {
                    str(entry.id): self._proofread_result(entry, valid=False, note="校对已停止") for entry in entries
                }
            pause_event.wait(0.05)
        if stop_event is not None and stop_event.is_set():
            return {str(entry.id): self._proofread_result(entry, valid=False, note="校对已停止") for entry in entries}
        from transbridge.ai_translator.project_terminology_adapter import plugin_id_from_entry

        candidates = tuple(
            PostProcessCandidate(
                run_id="proofread-preview",
                entry_key=entry.identity,
                before_revision=entry.revision,
                original=entry.original or "",
                before_text=entry.translation or "",
                text=entry.translation or "",
                stage=entry.stage,
                context=entry.context or "",
                report_details=(
                    (("terminology_plugin_id", plugin_id),)
                    if (plugin_id := plugin_id_from_entry(entry)) is not None
                    else ()
                ),
            )
            for entry in entries
        )

        def on_batch(completed: int, count: int, message: str) -> None:
            if progress_callback:
                progress_callback("proofread", completed, count, message)

        monitor_done = threading.Event()

        def monitor() -> None:
            while not monitor_done.wait(0.05):
                stopped = stop_event is not None and stop_event.is_set()
                if stopped:
                    self._proofread_stage.cancel()
                    return

        monitor_thread = threading.Thread(target=monitor, daemon=True)
        monitor_thread.start()
        try:
            runner = getattr(self._proofread_stage, "run", None)
            if callable(runner):
                outcome = runner(candidates, max_workers=max_workers, progress_callback=on_batch)
            else:  # compatibility with the initial stage implementation
                outcome = self._proofread_stage(candidates)
        finally:
            monitor_done.set()
        notes_by_key: dict[object, list[str]] = {}
        global_notes: list[str] = []
        for diagnostic in outcome.diagnostics:
            details = dict(diagnostic.details)
            key_data = details.get("entry_key")
            if isinstance(key_data, dict):
                from transbridge.application.io import EntryKey

                try:
                    key = EntryKey.from_dict(key_data)
                except (KeyError, TypeError, ValueError):
                    global_notes.append(diagnostic.message)
                else:
                    notes_by_key.setdefault(key, []).append(diagnostic.message)
            else:
                global_notes.append(diagnostic.message)
            if log_callback:
                log_callback(f"[{diagnostic.code}] {diagnostic.message}")
        by_key = {candidate.entry_key: candidate for candidate in outcome.candidates}
        projected = {}
        for entry in entries:
            candidate = by_key.get(entry.identity)
            valid = candidate is not None and candidate.accepted and "proofread" in candidate.phases
            note = "；".join((*global_notes, *notes_by_key.get(entry.identity, ())))
            projected[str(entry.id)] = self._proofread_result(
                entry,
                valid=valid,
                translation=candidate.text if valid and candidate is not None else None,
                note=note,
            )
        if progress_callback:
            progress_callback("proofread", total, total, f"校对完成 {total}/{total}")
        return projected

    @staticmethod
    def _proofread_result(
        entry: object,
        *,
        valid: bool,
        translation: str | None = None,
        note: str = "",
    ) -> ProofreadResult:
        final_translation = translation if valid and translation is not None else entry.translation or ""
        return ProofreadResult(
            entry_id=str(entry.id),
            entry_key=str(entry.key),
            original_translation=entry.translation or "",
            polished_translation=final_translation,
            confidence=1.0 if valid else 0.0,
            needs_arbitration=False,
            note=note,
            verdict="pass" if valid else "failed",
        )

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
