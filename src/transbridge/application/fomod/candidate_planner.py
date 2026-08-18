"""Pure FOMOD candidate planning across migration, TM and AI fallback."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from transbridge.application.contracts import Diagnostic
from transbridge.application.io import EntryKey, EntrySnapshot, Provenance, StageOperation, StagePolicy
from transbridge.migrator import KeyMigrationPlan, MigrationDisposition
from transbridge.translation_memory import (
    TmConflictPolicy,
    TranslationMemoryQuery,
    TranslationMemoryQueryService,
)

from .translation import (
    CandidateOrigin,
    FomodCandidateConflict,
    FomodCandidateSet,
    FomodTranslationCandidate,
)


class AiFallbackPort(Protocol):
    def translate(
        self,
        entry: EntrySnapshot,
        *,
        target_locale: str,
        cancellation: object | None,
    ) -> str | None: ...


class FomodCandidatePlanner:
    def __init__(
        self,
        tm: TranslationMemoryQueryService,
        *,
        stage_policy: StagePolicy | None = None,
        ai: AiFallbackPort | None = None,
    ) -> None:
        self._tm = tm
        self._stage_policy = stage_policy or StagePolicy()
        self._ai = ai

    def plan(
        self,
        *,
        run_id: str,
        entries: Sequence[EntrySnapshot],
        migration: KeyMigrationPlan,
        source_locale: str,
        target_locale: str,
        source_fingerprint: str,
        tm_policy: TmConflictPolicy = TmConflictPolicy.REQUIRE_CONFIRMATION,
        tm_selections: Mapping[EntryKey, str] | None = None,
        migration_selections: Mapping[EntryKey, str] | None = None,
        cancellation: object | None = None,
    ) -> FomodCandidateSet:
        selected: list[FomodTranslationCandidate] = []
        conflicts: list[FomodCandidateConflict] = []
        unresolved: list[EntryKey] = []
        blockers: list[Diagnostic] = []
        diagnostics: list[Diagnostic] = list(migration.diagnostics)
        migration_by_key: dict[EntryKey, list] = {}
        for candidate in migration.candidates:
            migration_by_key.setdefault(candidate.target_key, []).append(candidate)
        migration_selections = migration_selections or {}
        tm_selections = tm_selections or {}

        if _cancelled(cancellation) or migration.cancelled:
            return FomodCandidateSet(run_id, cancelled=True)

        for entry in entries:
            if _cancelled(cancellation) or migration.cancelled:
                return FomodCandidateSet(
                    run_id,
                    tuple(selected),
                    tuple(conflicts),
                    tuple(unresolved),
                    tuple(blockers),
                    tuple(diagnostics),
                    True,
                )
            stage = self._stage_policy.evaluate(
                entry.stage,
                entry.translation,
                StageOperation.TM_READ,
                original=entry.original,
            )
            if stage.blocks_publish:
                if stage.diagnostic is not None:
                    blockers.append(stage.diagnostic)
                continue
            if not stage.include_tm:
                continue
            if entry.translation:
                continue

            migration_options = migration_by_key.get(entry.entry_key, [])
            chosen = _migration_selection(
                run_id,
                entry,
                migration_options,
                migration_selections.get(entry.entry_key),
            )
            if isinstance(chosen, FomodTranslationCandidate):
                selected.append(chosen)
                continue
            if isinstance(chosen, FomodCandidateConflict):
                conflicts.append(chosen)
                continue

            dictionary_id = tm_selections.get(entry.entry_key)
            policy = TmConflictPolicy.EXPLICIT_DICTIONARY if dictionary_id else tm_policy
            tm_result = self._tm.query(
                TranslationMemoryQuery(
                    entry.entry_key,
                    entry.original,
                    source_locale,
                    target_locale,
                    entry.stage,
                    source_fingerprint,
                    policy,
                    dictionary_id,
                ),
                cancellation,
            )
            diagnostics.extend(tm_result.diagnostics)
            if tm_result.cancelled:
                return FomodCandidateSet(
                    run_id,
                    tuple(selected),
                    tuple(conflicts),
                    tuple(unresolved),
                    tuple(blockers),
                    tuple(diagnostics),
                    True,
                )
            if tm_result.blocks_publish:
                blockers.extend(tm_result.diagnostics)
                continue
            if tm_result.selected is not None:
                candidate = tm_result.selected
                chain = candidate.provenance or (
                    Provenance(run_id, "translation-memory", f"dictionary:{candidate.dictionary_id}"),
                )
                selected.append(
                    FomodTranslationCandidate(
                        entry.entry_key,
                        entry.revision,
                        candidate.translation,
                        1,
                        CandidateOrigin.TRANSLATION_MEMORY,
                        chain,
                        candidate.reasons,
                    )
                )
                continue
            if tm_result.candidates:
                alternatives = tuple(
                    FomodTranslationCandidate(
                        entry.entry_key,
                        entry.revision,
                        item.translation,
                        1,
                        CandidateOrigin.TRANSLATION_MEMORY,
                        item.provenance
                        or (Provenance(run_id, "translation-memory", f"dictionary:{item.dictionary_id}"),),
                        item.reasons,
                    )
                    for item in tm_result.candidates
                )
                conflicts.append(
                    FomodCandidateConflict(
                        entry.entry_key,
                        alternatives,
                        "translation-memory confirmation required",
                    )
                )
                continue

            ai_decision = self._stage_policy.evaluate(
                entry.stage,
                entry.translation,
                StageOperation.AI,
                original=entry.original,
            )
            if self._ai is not None and ai_decision.include_ai:
                text = self._ai.translate(
                    entry,
                    target_locale=target_locale,
                    cancellation=cancellation,
                )
                if _cancelled(cancellation):
                    return FomodCandidateSet(
                        run_id,
                        tuple(selected),
                        tuple(conflicts),
                        tuple(unresolved),
                        tuple(blockers),
                        tuple(diagnostics),
                        True,
                    )
                if text:
                    selected.append(
                        FomodTranslationCandidate(
                            entry.entry_key,
                            entry.revision,
                            text,
                            1,
                            CandidateOrigin.AI,
                            (Provenance(run_id, "fomod-ai", "ai-fallback"),),
                            ("tm_and_migration_unresolved",),
                        )
                    )
                    continue
            unresolved.append(entry.entry_key)

        return FomodCandidateSet(
            run_id,
            tuple(selected),
            tuple(conflicts),
            tuple(unresolved),
            tuple(blockers),
            tuple(diagnostics),
        )


def _migration_selection(
    run_id: str,
    entry: EntrySnapshot,
    options: list,
    selected_candidate_id: str | None,
) -> FomodTranslationCandidate | FomodCandidateConflict | None:
    alternatives = tuple(
        FomodTranslationCandidate(
            entry.entry_key,
            entry.revision,
            item.translation,
            1,
            CandidateOrigin.KEY_MIGRATION,
            item.provenance or (Provenance(run_id, "key-migrator", "key-migration"),),
            item.reasons,
        )
        for item in options
    )
    if selected_candidate_id:
        selected = next(
            (item for item in alternatives if item.candidate_id == selected_candidate_id),
            None,
        )
        if selected is not None:
            return selected
    exact = [
        candidate
        for candidate, source in zip(alternatives, options, strict=True)
        if source.disposition is MigrationDisposition.EXACT
    ]
    if len(exact) == 1 and len({item.translation for item in alternatives}) == 1:
        return exact[0]
    if alternatives:
        return FomodCandidateConflict(
            entry.entry_key,
            alternatives,
            "key migration confirmation required",
        )
    return None


def _cancelled(signal: object | None) -> bool:
    if signal is None:
        return False
    state = getattr(signal, "is_cancelled", None)
    if state is not None:
        return bool(state() if callable(state) else state)
    is_set = getattr(signal, "is_set", None)
    return bool(is_set()) if callable(is_set) else False
