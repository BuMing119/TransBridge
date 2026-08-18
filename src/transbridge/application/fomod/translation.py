"""FOMOD translation candidates and the single collection commit boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
from typing import Protocol

from transbridge.application.contracts import Diagnostic, OperationOutcome, RequestContext
from transbridge.application.io import (
    ChangeSet,
    CollectionMutationPort,
    EntryKey,
    EntryPatch,
    EntryRevision,
    MutationResult,
    MutationStatus,
    Provenance,
)


class CandidateOrigin(StrEnum):
    KEY_MIGRATION = "key_migration"
    TRANSLATION_MEMORY = "translation_memory"
    AI = "ai"


@dataclass(frozen=True, slots=True)
class FomodTranslationCandidate:
    entry_key: EntryKey
    before_revision: EntryRevision
    translation: str
    resulting_stage: int
    origin: CandidateOrigin
    source_chain: tuple[Provenance, ...]
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.translation:
            raise ValueError("FOMOD translation candidate must not be empty")
        if not self.source_chain:
            raise ValueError("FOMOD translation candidate requires provenance")

    @property
    def candidate_id(self) -> str:
        value = "|".join((
            self.entry_key.serialize(),
            str(self.before_revision.value),
            self.origin.value,
            self.translation,
            ",".join(f"{item.actor}:{item.source}" for item in self.source_chain),
        ))
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class FomodCandidateConflict:
    entry_key: EntryKey
    alternatives: tuple[FomodTranslationCandidate, ...]
    reason: str

    def __post_init__(self) -> None:
        if not self.alternatives:
            raise ValueError("candidate conflict requires at least one confirmable alternative")
        if any(item.entry_key != self.entry_key for item in self.alternatives):
            raise ValueError("candidate conflict alternatives must share one EntryKey")


@dataclass(frozen=True, slots=True)
class FomodCandidateSet:
    run_id: str
    selected: tuple[FomodTranslationCandidate, ...] = ()
    conflicts: tuple[FomodCandidateConflict, ...] = ()
    unresolved: tuple[EntryKey, ...] = ()
    blockers: tuple[Diagnostic, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    cancelled: bool = False

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("candidate set run_id must not be empty")
        keys = tuple(item.entry_key for item in self.selected)
        if len(keys) != len(set(keys)):
            raise ValueError("candidate set can select at most one candidate per EntryKey")
        unresolved = set(self.unresolved)
        if unresolved.intersection(keys):
            raise ValueError("selected and unresolved candidate keys must be disjoint")
        conflict_keys = {item.entry_key for item in self.conflicts}
        if conflict_keys.intersection(keys):
            raise ValueError("selected and conflicting candidate keys must be disjoint")

    @property
    def source_chain(self) -> tuple[tuple[EntryKey, tuple[Provenance, ...]], ...]:
        return tuple((item.entry_key, item.source_chain) for item in self.selected)


@dataclass(frozen=True, slots=True)
class FomodCandidateCommitReport:
    outcome: OperationOutcome
    mutation: MutationResult | None = None
    diagnostics: tuple[Diagnostic, ...] = ()


class CancellationSignal(Protocol):
    @property
    def is_cancelled(self) -> bool: ...


class CandidateCommitGuard(Protocol):
    def commit(self, run_id: str, mutation) -> bool: ...


class CommitFomodCandidates:
    """Apply all selected candidates through exactly one ChangeSet call."""

    def __init__(
        self,
        mutation_port: CollectionMutationPort,
        *,
        commit_guard: CandidateCommitGuard | None = None,
    ) -> None:
        self._mutation_port = mutation_port
        self._commit_guard = commit_guard

    def execute(
        self,
        candidates: FomodCandidateSet,
        context: RequestContext,
        cancellation: object | None = None,
    ) -> FomodCandidateCommitReport:
        if _cancelled(cancellation) or candidates.cancelled:
            return FomodCandidateCommitReport(OperationOutcome.CANCELLED)
        if candidates.blockers:
            return FomodCandidateCommitReport(
                OperationOutcome.FAILED,
                diagnostics=candidates.blockers,
            )
        if not candidates.selected:
            incomplete = candidates.unresolved or candidates.conflicts
            outcome = OperationOutcome.PARTIAL if incomplete else OperationOutcome.COMPLETED
            return FomodCandidateCommitReport(outcome, diagnostics=candidates.diagnostics)
        if context.run_id != candidates.run_id:
            return FomodCandidateCommitReport(
                OperationOutcome.FAILED,
                diagnostics=(
                    Diagnostic(
                        "FOMOD_COMMIT_RUN_MISMATCH",
                        "Candidate set and trusted request context run ids do not match.",
                    ),
                ),
            )
        patches = tuple(
            EntryPatch.create(
                item.entry_key,
                translation=item.translation,
                stage=item.resulting_stage,
            )
            for item in candidates.selected
        )
        expected = tuple((item.entry_key, item.before_revision) for item in candidates.selected)
        source_summary = tuple(sorted({source.source for item in candidates.selected for source in item.source_chain}))
        change_set = ChangeSet(
            candidates.run_id,
            patches,
            expected,
            Provenance(
                candidates.run_id,
                context.owner_id,
                "fomod-candidate-set",
                metadata=(("source_chain", source_summary),),
            ),
        )
        if _cancelled(cancellation):
            return FomodCandidateCommitReport(OperationOutcome.CANCELLED)
        holder: dict[str, MutationResult] = {}

        def mutation() -> None:
            if _cancelled(cancellation):
                return
            holder["result"] = self._mutation_port.apply(change_set, context)

        if self._commit_guard is not None:
            accepted = self._commit_guard.commit(candidates.run_id, mutation)
            if not accepted or "result" not in holder:
                return FomodCandidateCommitReport(
                    OperationOutcome.CANCELLED,
                    diagnostics=(
                        Diagnostic(
                            "FOMOD_COMMIT_GUARD_REJECTED",
                            "The runtime rejected a late candidate commit.",
                        ),
                    ),
                )
        else:
            mutation()
            if "result" not in holder:
                return FomodCandidateCommitReport(OperationOutcome.CANCELLED)
        result = holder["result"]
        if result.status is MutationStatus.APPLIED:
            incomplete = candidates.unresolved or candidates.conflicts
            outcome = OperationOutcome.PARTIAL if incomplete else OperationOutcome.COMPLETED
        elif result.status is MutationStatus.CONFLICT:
            outcome = OperationOutcome.PARTIAL
        else:
            outcome = OperationOutcome.FAILED
        return FomodCandidateCommitReport(
            outcome,
            result,
            (*candidates.diagnostics, *result.diagnostics),
        )


def _cancelled(signal: object | None) -> bool:
    if signal is None:
        return False
    state = getattr(signal, "is_cancelled", None)
    if state is not None:
        return bool(state() if callable(state) else state)
    is_set = getattr(signal, "is_set", None)
    return bool(is_set()) if callable(is_set) else False
