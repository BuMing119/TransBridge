"""Locale/source-aware read-only translation-memory planner."""

from __future__ import annotations

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity
from transbridge.application.io import StageOperation, StagePolicy
from transbridge.converter.translation_entry import _normalize_text

from .contracts import (
    TmConflictPolicy,
    TmMatchStatus,
    TranslationMemoryCandidate,
    TranslationMemoryQuery,
    TranslationMemoryQueryResult,
)
from .manager import TranslationMemoryManager
from .model import SCOPE_PROJECT, Dictionary, DictionaryEntry


class TranslationMemoryQueryService:
    """Create candidates without mutating dictionaries or entry collections."""

    def __init__(
        self,
        manager: TranslationMemoryManager,
        *,
        stage_policy: StagePolicy | None = None,
    ) -> None:
        self._manager = manager
        self._stage_policy = stage_policy or StagePolicy()

    def query(
        self,
        request: TranslationMemoryQuery,
        cancellation: object | None = None,
    ) -> TranslationMemoryQueryResult:
        if _cancelled(cancellation):
            return TranslationMemoryQueryResult(cancelled=True)
        stage = self._stage_policy.evaluate(
            request.stage,
            "",
            StageOperation.TM_READ,
            original=request.original,
        )
        if stage.blocks_publish:
            diagnostics = (stage.diagnostic,) if stage.diagnostic is not None else ()
            return TranslationMemoryQueryResult(
                diagnostics=diagnostics,
                blocks_publish=True,
            )
        if not stage.include_tm:
            return TranslationMemoryQueryResult()

        candidates: list[TranslationMemoryCandidate] = []
        diagnostics: list[Diagnostic] = []
        for dictionary in self._manager.snapshot_dictionaries():
            if _cancelled(cancellation):
                return TranslationMemoryQueryResult(
                    candidates=tuple(candidates),
                    diagnostics=tuple(diagnostics),
                    cancelled=True,
                )
            self._collect_dictionary(request, dictionary, candidates, diagnostics)

        candidates.sort(key=_priority, reverse=True)
        if not candidates:
            return TranslationMemoryQueryResult(diagnostics=tuple(diagnostics))
        selected, requires_confirmation, arbitration_diagnostics = _arbitrate(request, candidates)
        diagnostics.extend(arbitration_diagnostics)
        return TranslationMemoryQueryResult(
            candidates=tuple(candidates),
            selected=selected,
            diagnostics=tuple(diagnostics),
            requires_confirmation=requires_confirmation,
        )

    @staticmethod
    def _collect_dictionary(
        request: TranslationMemoryQuery,
        dictionary: Dictionary,
        candidates: list[TranslationMemoryCandidate],
        diagnostics: list[Diagnostic],
    ) -> None:
        normalized = _normalize_text(request.original)
        key_matches = (
            request.entry_key.serialize(),
            request.entry_key.local_key,
        )
        found: list[tuple[DictionaryEntry, str]] = []
        seen: set[str] = set()
        for key in key_matches:
            index = dictionary.key_index.get(key)
            if index is None:
                continue
            entry_id = str(index.get("entry_id", ""))
            entry = dictionary.entries.get(entry_id)
            if entry is not None and entry_id not in seen:
                seen.add(entry_id)
                found.append((entry, "key"))
        text_index = dictionary.text_index.get(normalized)
        if text_index is not None:
            entry_id = str(text_index.get("entry_id", ""))
            entry = dictionary.entries.get(entry_id)
            if entry is not None and (
                entry_id not in seen or entry.source_namespace != request.entry_key.namespace.value
            ):
                found.append((entry, "text"))

        for entry, matched_via in found:
            dictionary_id = entry.dictionary_id or dictionary.dictionary_id or dictionary.mod_file_id
            if not entry.enabled or not entry.source_locale or not entry.target_locale:
                diagnostics.append(
                    Diagnostic(
                        "TM_LEGACY_LOCALE_UNKNOWN",
                        "A legacy dictionary entry has no proven locale and was not auto-enabled.",
                        DiagnosticSeverity.WARNING,
                        details=(("dictionary_id", dictionary_id),),
                    )
                )
                continue
            if (
                entry.source_locale.casefold() != request.source_locale.casefold()
                or entry.target_locale.casefold() != request.target_locale.casefold()
            ):
                continue
            source_exact = entry.source_namespace == request.entry_key.namespace.value
            if matched_via == "key" and not source_exact:
                continue

            stale_reasons: list[str] = []
            if matched_via == "key" and _normalize_text(entry.original) != normalized:
                stale_reasons.append("source_text_changed")
            if source_exact and entry.source_fingerprint != request.source_fingerprint:
                stale_reasons.append("source_fingerprint_changed")
            reasons = list(stale_reasons)
            if matched_via == "text" and not source_exact:
                reasons.append("cross_source_text_fallback")
            if stale_reasons:
                status = TmMatchStatus.STALE
            elif matched_via == "key":
                status = TmMatchStatus.EXACT
            else:
                status = TmMatchStatus.TEXT
            candidates.append(
                TranslationMemoryCandidate(
                    request.entry_key,
                    entry.translation,
                    dictionary_id,
                    entry.dictionary_revision or dictionary.revision,
                    dictionary.scope,
                    status,
                    matched_via,
                    tuple(reasons) or (f"matched_by_{matched_via}",),
                    entry.provenance,
                )
            )


def _arbitrate(
    request: TranslationMemoryQuery,
    candidates: list[TranslationMemoryCandidate],
) -> tuple[TranslationMemoryCandidate | None, bool, tuple[Diagnostic, ...]]:
    usable = [item for item in candidates if item.match_status is not TmMatchStatus.STALE]
    if request.conflict_policy is TmConflictPolicy.EXPLICIT_DICTIONARY:
        selected = next(
            (item for item in candidates if item.dictionary_id == request.selected_dictionary_id),
            None,
        )
        if selected is None:
            return (
                None,
                True,
                (
                    Diagnostic(
                        "TM_ARBITRATION_SELECTION_INVALID",
                        "The selected dictionary is not one of the current candidates.",
                    ),
                ),
            )
        return selected, False, ()
    if not usable:
        return (
            None,
            bool(candidates),
            (
                Diagnostic(
                    "TM_STALE_CONFIRMATION_REQUIRED",
                    "All translation-memory candidates are stale and require explicit confirmation.",
                    DiagnosticSeverity.WARNING,
                ),
            ),
        )

    translations = {item.translation for item in usable}
    if len(translations) == 1:
        return usable[0], False, ()
    if request.conflict_policy is TmConflictPolicy.PREFER_PROJECT:
        project = next((item for item in usable if item.dictionary_scope == SCOPE_PROJECT), None)
        if project is not None:
            return project, False, ()
    return (
        None,
        True,
        (
            Diagnostic(
                "TM_CONFLICT_CONFIRMATION_REQUIRED",
                "Translation-memory candidates disagree and require explicit arbitration.",
                DiagnosticSeverity.WARNING,
                details=(("candidate_count", len(usable)),),
            ),
        ),
    )


def _priority(candidate: TranslationMemoryCandidate) -> tuple[int, int, int, int, str]:
    return (
        int(candidate.match_status is TmMatchStatus.EXACT),
        int("cross_source_text_fallback" not in candidate.reasons),
        int(candidate.dictionary_scope == SCOPE_PROJECT),
        candidate.dictionary_revision,
        candidate.dictionary_id,
    )


def _cancelled(signal: object | None) -> bool:
    if signal is None:
        return False
    state = getattr(signal, "is_cancelled", None)
    if state is not None:
        return bool(state() if callable(state) else state)
    is_set = getattr(signal, "is_set", None)
    return bool(is_set()) if callable(is_set) else False
