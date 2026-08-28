"""Immutable post-processing candidate pipeline and canonical reports.

The legacy GUI processor still contains UI-oriented adapters.  This module is
the application boundary used by new entrypoints: stages only receive and
return candidates, while a caller owns the single guarded collection commit.

A run may persist an atomic post-process checkpoint after every completed
stage; resuming from a checkpoint replays no LLM calls for already completed
stages.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
import math
import time
from typing import Any, Protocol

from transbridge.application.contracts import (
    Diagnostic,
    DiagnosticSeverity,
    ErrorCategory,
    OperationCounts,
    OperationOutcome,
    OperationResult,
)
from transbridge.application.io import EntryKey, EntryRevision, StageOperation, StagePolicyPort
from transbridge.application.security import SecretRedactor

from .postprocess_checkpoint import (
    PostProcessCheckpoint,
    PostProcessCheckpointPort,
    checkpoint_entry_from_candidate,
)
from .workload_models import TranslationInput, canonical_hash, translation_input_fingerprint

_REPORT_SENSITIVE_KEYS = frozenset({
    "api_key",
    "apikey",
    "authorization",
    "password",
    "passwd",
    "prompt",
    "secret",
    "token",
})


def _normalize_report_value(value: object) -> Any:
    """Return a detached, JSON-safe and recursively redacted report value."""
    redactor = SecretRedactor.default()
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return redactor.redact_text(value)
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            safe_key = redactor.redact_text(str(key))
            if str(key).casefold() in _REPORT_SENSITIVE_KEYS:
                normalized[safe_key] = redactor.REDACTED
            else:
                normalized[safe_key] = _normalize_report_value(item)
        return normalized
    if isinstance(value, (tuple, list)):
        return [_normalize_report_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_normalize_report_value(item) for item in value), key=str)
    return redactor.redact_text(str(value))


def _normalize_report_mapping(value: Mapping[object, object]) -> dict[str, Any]:
    normalized = _normalize_report_value(value)
    if not isinstance(normalized, dict):  # pragma: no cover - guarded by Mapping input
        raise TypeError("report value must normalize to an object")
    return normalized


def _normalize_diagnostic(diagnostic: Diagnostic) -> Diagnostic:
    details = _normalize_report_mapping(dict(diagnostic.details))
    return Diagnostic(
        code=diagnostic.code,
        message=str(_normalize_report_value(diagnostic.message)),
        severity=diagnostic.severity,
        category=diagnostic.category,
        retryable=diagnostic.retryable,
        details=tuple(sorted(details.items())),
    )


@dataclass(frozen=True, slots=True)
class PostProcessCandidate:
    """One immutable value as it moves through the post-processing stages."""

    run_id: str
    entry_key: EntryKey
    before_revision: EntryRevision
    original: str
    before_text: str
    text: str
    stage: int
    phases: tuple[str, ...] = ()
    accepted: bool = True
    context: str = ""
    report_details: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        """Detach, redact and normalize report-only metadata for durable JSON."""
        normalized = _normalize_report_mapping(dict(self.report_details))
        object.__setattr__(self, "report_details", tuple(sorted(normalized.items())))

    def with_text(self, text: str, phase: str) -> PostProcessCandidate:
        if not text:
            raise ValueError("post-process candidates must not have an empty text")
        return replace(self, text=text, phases=(*self.phases, phase))

    def with_accepted(self, accepted: bool) -> PostProcessCandidate:
        return replace(self, accepted=accepted)

    def with_report_details(self, details: Mapping[str, Any]) -> PostProcessCandidate:
        return replace(self, report_details=tuple(details.items()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_key": self.entry_key.to_dict(),
            "before_revision": self.before_revision.value,
            "original": self.original,
            "before": self.before_text,
            "candidate": self.text,
            "stage": self.stage,
            "phases": list(self.phases),
            "accepted": self.accepted,
            "context": self.context,
            "report_details": _normalize_report_mapping(dict(self.report_details)),
        }


@dataclass(frozen=True, slots=True)
class PostProcessStageOutcome:
    phase: str
    candidates: tuple[PostProcessCandidate, ...]
    diagnostics: tuple[Diagnostic, ...] = ()
    duration_ms: int = 0

    @property
    def failed(self) -> bool:
        return any(
            diagnostic.category in {ErrorCategory.INTERNAL, ErrorCategory.EXTERNAL} for diagnostic in self.diagnostics
        )


class PostProcessStagePort(Protocol):
    phase: str

    def __call__(self, candidates: tuple[PostProcessCandidate, ...]) -> PostProcessStageOutcome: ...


@dataclass(frozen=True, slots=True)
class ReportSnapshot:
    """The only report source for UI, Excel, and durable history."""

    schema: str
    run_id: str
    outcome: OperationOutcome
    input_count: int
    accepted_count: int
    candidates: tuple[PostProcessCandidate, ...]
    stage_outcomes: tuple[PostProcessStageOutcome, ...]
    diagnostics: tuple[Diagnostic, ...]
    issue_count: int = 0
    failure_count: int = 0
    timing_ms: tuple[tuple[str, int], ...] = ()
    run_spec_summary: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Detach and redact all metadata crossing the durable report boundary."""
        normalized_diagnostics = tuple(_normalize_diagnostic(item) for item in self.diagnostics)
        normalized_stages = tuple(
            replace(
                outcome,
                diagnostics=tuple(_normalize_diagnostic(item) for item in outcome.diagnostics),
            )
            for outcome in self.stage_outcomes
        )
        object.__setattr__(self, "diagnostics", normalized_diagnostics)
        object.__setattr__(self, "stage_outcomes", normalized_stages)
        object.__setattr__(self, "run_spec_summary", _normalize_report_mapping(self.run_spec_summary))

    @property
    def fingerprint(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "run_id": self.run_id,
            "outcome": self.outcome.value,
            "counts": {"input": self.input_count, "accepted": self.accepted_count},
            "entries": [candidate.to_dict() for candidate in self.candidates],
            "stages": [
                {
                    "phase": outcome.phase,
                    "duration_ms": outcome.duration_ms,
                    "entries": [candidate.to_dict() for candidate in outcome.candidates],
                    "diagnostics": [
                        _normalize_report_mapping(diagnostic.to_dict()) for diagnostic in outcome.diagnostics
                    ],
                }
                for outcome in self.stage_outcomes
            ],
            "diagnostics": [_normalize_report_mapping(diagnostic.to_dict()) for diagnostic in self.diagnostics],
            "issues": self.issue_count,
            "failures": self.failure_count,
            "timing_ms": list(self.timing_ms),
            "run_spec_summary": _normalize_report_mapping(self.run_spec_summary),
        }


@dataclass(frozen=True, slots=True)
class PostProcessRunResult:
    """Operation contract plus an always-available terminal report snapshot."""

    operation_result: OperationResult[ReportSnapshot]
    report_snapshot: ReportSnapshot


class PostProcessWorkload:
    """Run candidate-only stages and return a commit-ready canonical snapshot."""

    def __init__(
        self,
        stages: tuple[PostProcessStagePort, ...],
        *,
        stage_policy: StagePolicyPort,
        stage_names: tuple[str, ...] | None = None,
        checkpoint_port: PostProcessCheckpointPort | None = None,
    ) -> None:
        if stage_names is not None and len(stage_names) != len(stages):
            raise ValueError("post-process stage names must match the number of stages")
        self._stages = stages
        self._stage_policy = stage_policy
        self._stage_names = stage_names or tuple(f"stage_{index}" for index in range(len(stages)))
        self._checkpoint_port = checkpoint_port

    def run(
        self,
        run_id: str,
        entries: tuple[TranslationInput, ...],
        *,
        is_cancelled: Callable[[], bool] = lambda: False,
        owner_id: str = "",
        expected_revisions: Mapping[EntryKey, EntryRevision] | None = None,
        resume_after_phase: str | None = None,
        run_spec_summary: Mapping[str, Any] | None = None,
    ) -> OperationResult[ReportSnapshot]:
        """Compatibility API returning only the canonical operation contract."""
        return self.run_with_snapshot(
            run_id,
            entries,
            is_cancelled=is_cancelled,
            owner_id=owner_id,
            expected_revisions=expected_revisions,
            resume_after_phase=resume_after_phase,
            run_spec_summary=run_spec_summary,
        ).operation_result

    def run_with_snapshot(
        self,
        run_id: str,
        entries: tuple[TranslationInput, ...],
        *,
        is_cancelled: Callable[[], bool] = lambda: False,
        owner_id: str = "",
        expected_revisions: Mapping[EntryKey, EntryRevision] | None = None,
        resume_after_phase: str | None = None,
        run_spec_summary: Mapping[str, Any] | None = None,
    ) -> PostProcessRunResult:
        input_fingerprint = translation_input_fingerprint(entries)
        checkpoint = self._resume_checkpoint(run_id, owner_id, input_fingerprint, resume_after_phase)

        candidates: list[PostProcessCandidate] = []
        diagnostics: list[Diagnostic] = []
        conflicts = 0
        for entry in entries:
            decision = self._stage_policy.evaluate(
                entry.stage, entry.translation, StageOperation.AI, original=entry.original
            )
            if decision.include_ai:
                revision_ok = expected_revisions is None or expected_revisions.get(entry.entry_key) in (
                    None,
                    entry.revision,
                )
                if not revision_ok:
                    conflicts += 1
                    diagnostics.append(
                        Diagnostic(
                            "REVISION_CONFLICT",
                            "The collection entry changed after this post-process run started.",
                            category=ErrorCategory.CONFLICT,
                            details=(("entry_key", entry.entry_key.to_dict()),),
                        )
                    )
                    continue
                candidates.append(
                    PostProcessCandidate(
                        run_id,
                        entry.entry_key,
                        entry.revision,
                        entry.original,
                        entry.translation,
                        entry.translation or entry.original,
                        entry.stage,
                        context=entry.context,
                        report_details=(
                            (("terminology_plugin_id", entry.terminology_plugin_id),)
                            if entry.terminology_plugin_id is not None
                            else ()
                        ),
                    )
                )
            elif decision.diagnostic is not None:
                diagnostics.append(decision.diagnostic)

        if checkpoint is not None and resume_after_phase is not None:
            current = self._resumed_candidates(checkpoint)
        else:
            current = tuple(candidates)

        outcomes: list[PostProcessStageOutcome] = []
        resume_index = self._resume_index(resume_after_phase)
        for index, stage in enumerate(self._stages):
            if resume_index is not None and index <= resume_index:
                outcomes.append(PostProcessStageOutcome(self._stage_names[index], current))
                continue
            if is_cancelled():
                result = self._result(
                    OperationOutcome.CANCELLED,
                    run_id,
                    entries,
                    current,
                    outcomes,
                    (
                        *diagnostics,
                        Diagnostic(
                            "POSTPROCESS_CANCELLED",
                            "Post-processing was cancelled.",
                            category=ErrorCategory.CANCELLED,
                        ),
                    ),
                    conflicts=conflicts,
                    run_spec_summary=run_spec_summary,
                )
                self._persist(run_id, owner_id, input_fingerprint, outcomes, current)
                return result
            started = time.perf_counter()
            try:
                stage_outcome = stage(current)
            except Exception as exc:
                diagnostic = Diagnostic(
                    "POSTPROCESS_STAGE_FAILED",
                    "A post-processing stage failed.",
                    category=ErrorCategory.INTERNAL,
                    details=(("error_type", type(exc).__name__),),
                )
                result = self._result(
                    OperationOutcome.FAILED,
                    run_id,
                    entries,
                    current,
                    outcomes,
                    (*diagnostics, diagnostic),
                    conflicts=conflicts,
                    run_spec_summary=run_spec_summary,
                )
                self._persist(run_id, owner_id, input_fingerprint, outcomes, current)
                return result
            duration_ms = int((time.perf_counter() - started) * 1000)
            if stage_outcome.candidates and any(item.run_id != run_id for item in stage_outcome.candidates):
                raise ValueError("post-process stage returned a candidate for another run")
            outcome = replace(stage_outcome, duration_ms=duration_ms)
            outcomes.append(outcome)
            diagnostics.extend(outcome.diagnostics)
            current = outcome.candidates
            if outcome.failed:
                result = self._result(
                    OperationOutcome.PARTIAL,
                    run_id,
                    entries,
                    current,
                    outcomes,
                    tuple(diagnostics),
                    conflicts=conflicts,
                    stage_failed=True,
                    run_spec_summary=run_spec_summary,
                )
                self._persist(run_id, owner_id, input_fingerprint, outcomes, current)
                return result
            self._persist(run_id, owner_id, input_fingerprint, outcomes, current)

        result = self._result(
            OperationOutcome.PARTIAL if conflicts else OperationOutcome.COMPLETED,
            run_id,
            entries,
            current,
            outcomes,
            tuple(diagnostics),
            conflicts=conflicts,
            run_spec_summary=run_spec_summary,
        )
        self._persist(run_id, owner_id, input_fingerprint, outcomes, current)
        return result

    def _resume_index(self, resume_after_phase: str | None) -> int | None:
        if resume_after_phase is None:
            return None
        if resume_after_phase not in self._stage_names:
            raise ValueError("post-process resume phase does not match the workload stages")
        return self._stage_names.index(resume_after_phase)

    def _resume_checkpoint(
        self,
        run_id: str,
        owner_id: str,
        input_fingerprint: str,
        resume_after_phase: str | None,
    ) -> PostProcessCheckpoint | None:
        if self._checkpoint_port is None or resume_after_phase is None:
            return None
        checkpoint = self._checkpoint_port.load(run_id)
        if checkpoint is None:
            return None
        checkpoint.validate(owner_id=owner_id, input_fingerprint=input_fingerprint)
        resume_index = self._resume_index(resume_after_phase)
        expected = self._stage_names[: resume_index + 1]
        if checkpoint.completed_phases[: len(expected)] != expected:
            raise ValueError("post-process resume phase does not match the checkpoint")
        return checkpoint

    def _resumed_candidates(self, checkpoint: PostProcessCheckpoint) -> tuple[PostProcessCandidate, ...]:
        from .postprocess_checkpoint import restore_candidates_from_checkpoint

        return restore_candidates_from_checkpoint(checkpoint)

    def _persist(
        self,
        run_id: str,
        owner_id: str,
        input_fingerprint: str,
        outcomes: list[PostProcessStageOutcome],
        candidates: tuple[PostProcessCandidate, ...],
    ) -> None:
        if self._checkpoint_port is None:
            return
        completed = tuple(outcome.phase for outcome in outcomes)
        entries = tuple(
            checkpoint_entry_from_candidate(
                candidate,
                phase=completed[-1] if completed else "scoped",
                accepted=candidate.accepted,
            )
            for candidate in candidates
        )
        existing = self._checkpoint_port.load(run_id)
        checkpoint = PostProcessCheckpoint(
            run_id=run_id,
            owner_id=owner_id,
            input_fingerprint=input_fingerprint,
            revision=0 if existing is None else existing.revision + 1,
            completed_phases=completed,
            entries=entries,
        )
        self._checkpoint_port.save(checkpoint)

    @staticmethod
    def _result(
        outcome: OperationOutcome,
        run_id: str,
        entries: tuple[TranslationInput, ...],
        candidates: tuple[PostProcessCandidate, ...],
        stages: list[PostProcessStageOutcome],
        diagnostics: tuple[Diagnostic, ...],
        *,
        conflicts: int = 0,
        stage_failed: bool = False,
        run_spec_summary: Mapping[str, Any] | None = None,
    ) -> PostProcessRunResult:
        accepted = sum(1 for candidate in candidates if candidate.accepted)
        failed_count = conflicts + (1 if stage_failed else 0)
        if outcome is OperationOutcome.PARTIAL and (accepted < 1 or failed_count < 1):
            # The operation contract forbids partial with no success or no failure.
            outcome = OperationOutcome.FAILED if failed_count >= 1 else OperationOutcome.CANCELLED

        issue_count = sum(
            1
            for diagnostic in diagnostics
            if diagnostic.severity in (DiagnosticSeverity.WARNING, DiagnosticSeverity.INFO)
            or diagnostic.category is ErrorCategory.CONFLICT
        )
        skipped = len(entries) - len(candidates)
        snapshot = ReportSnapshot(
            "transbridge.postprocess-report.v1",
            run_id,
            outcome,
            len(entries),
            accepted,
            candidates,
            tuple(stages),
            diagnostics,
            issue_count=issue_count,
            failure_count=failed_count,
            timing_ms=tuple((stage.phase, stage.duration_ms) for stage in stages),
            run_spec_summary=dict(run_spec_summary or {}),
        )
        if outcome is OperationOutcome.CANCELLED:
            verdict = OperationCounts(cancelled=max(1, len(candidates)), skipped=skipped)
            operation_result = OperationResult(
                outcome,
                None,
                diagnostics=snapshot.diagnostics,
                counts=verdict,
                run_id=run_id,
            )
            return PostProcessRunResult(operation_result, snapshot)
        if outcome is OperationOutcome.FAILED:
            verdict = OperationCounts(failed=max(1, failed_count), skipped=skipped)
            operation_result = OperationResult(
                outcome,
                None,
                diagnostics=snapshot.diagnostics,
                counts=verdict,
                run_id=run_id,
            )
            return PostProcessRunResult(operation_result, snapshot)
        verdict = OperationCounts(
            succeeded=accepted,
            failed=failed_count,
            skipped=skipped,
        )
        operation_result = OperationResult(
            outcome,
            snapshot,
            diagnostics=snapshot.diagnostics,
            counts=verdict,
            run_id=run_id,
        )
        return PostProcessRunResult(operation_result, snapshot)
