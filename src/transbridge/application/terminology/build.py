"""Deterministic full-build coordinator for project terminology."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from typing import Protocol

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import ParseRequest, ParseResult, SourceNamespace, TranslationIoUseCase

from .corpus import EvidenceAssembler, SourceCorpusFragment
from .extraction import CancellationPort, TerminologyExtractionService
from .identity import build_key, canonical_digest
from .input_capture import BuildInputSnapshot, CapturedSource
from .models import (
    BuildCompleteness,
    BuildResult,
    BuildResultRef,
    BuildSummary,
    TermCandidate,
    TermDecision,
)
from .ports import TerminologyRepositoryPort
from .reducer import (
    CanonicalTerminologyReducer,
    LogicalTerminologyFragment,
    ManualBaselineReconciler,
    reduce_fragments,
)


class SourceBuildStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class SourceBuildRecord:
    source_id: str
    format_id: str
    adapter_id: str
    adapter_version: str
    status: SourceBuildStatus
    entry_count: int
    duration_seconds: float
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FullBuildOutcome:
    result: BuildResult
    source_records: tuple[SourceBuildRecord, ...]
    effective_candidates: tuple[TermCandidate, ...]
    review_term_ids: tuple[str, ...]


class FullBuildError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class FullBuildCancelled(FullBuildError):
    def __init__(self) -> None:
        super().__init__("TERMINOLOGY_BUILD_CANCELLED", "terminology build was cancelled before freezing")


class RegisteredSourceParserPort(Protocol):
    def parse(self, source: CapturedSource, context: RequestContext) -> ParseResult: ...


class TranslationIoRegisteredSourceParser:
    """Narrow adapter from captured registrations to the shared I/O use case."""

    def __init__(self, io: TranslationIoUseCase) -> None:
        self._io = io

    def parse(self, source: CapturedSource, context: RequestContext) -> ParseResult:
        request = ParseRequest(
            source=source.lease.snapshot.source,
            context=context,
            format_hint=source.registration.format_id,
            source_namespace=SourceNamespace(f"project-source:{source.registration.source_id}"),
            options=source.parse_options,
        )
        return self._io.parse(request)


class TerminologyFullBuilder:
    def __init__(
        self,
        parser: RegisteredSourceParserPort,
        repository: TerminologyRepositoryPort,
        *,
        assembler: EvidenceAssembler | None = None,
        extraction: TerminologyExtractionService | None = None,
        reducer: CanonicalTerminologyReducer | None = None,
        reconciler: ManualBaselineReconciler | None = None,
    ) -> None:
        self._parser = parser
        self._repository = repository
        self._assembler = assembler or EvidenceAssembler()
        self._extraction = extraction or TerminologyExtractionService()
        self._reducer = reducer or CanonicalTerminologyReducer()
        self._reconciler = reconciler or ManualBaselineReconciler()

    def build(
        self,
        snapshot: BuildInputSnapshot,
        context: RequestContext,
        *,
        baseline_decisions: tuple[TermDecision, ...] = (),
        llm_enabled: bool = False,
        cancellation: CancellationPort | None = None,
    ) -> FullBuildOutcome:
        fragments: list[SourceCorpusFragment] = []
        records: list[SourceBuildRecord] = []
        any_readable = False
        for source in sorted(snapshot.sources, key=lambda item: item.registration.source_id):
            if _cancelled(cancellation):
                raise FullBuildCancelled
            fragment, record = self.parse_source(source, context)
            records.append(record)
            if record.status is SourceBuildStatus.CANCELLED:
                raise FullBuildCancelled
            if fragment is None:
                continue
            any_readable = True
            fragments.append(fragment)
        if not any_readable:
            raise FullBuildError("TERMINOLOGY_ALL_SOURCES_FAILED", "no registered terminology source was readable")
        logical = self.build_logical_fragment(
            component_id="full",
            snapshot=snapshot,
            fragments=tuple(fragments),
            relations=snapshot.relations,
            llm_enabled=llm_enabled,
            cancellation=cancellation,
        )
        return self.freeze_fragments(
            snapshot,
            (logical,),
            tuple(records),
            baseline_decisions=baseline_decisions,
        )

    def build_logical_fragment(
        self,
        *,
        component_id: str,
        snapshot: BuildInputSnapshot,
        fragments: tuple[SourceCorpusFragment, ...],
        relations: tuple,
        llm_enabled: bool,
        cancellation: CancellationPort | None = None,
    ) -> LogicalTerminologyFragment:
        assembly = self._assembler.assemble(
            project_id=snapshot.project_id,
            variant_id=snapshot.variant_id,
            fragments=fragments,
            relations=relations,
            variant_snapshot=snapshot.variant_snapshot,
        )
        extraction = self._extraction.extract(
            assembly.evidence,
            llm_enabled=llm_enabled,
            cancellation=cancellation,
        )
        if extraction.cancelled or _cancelled(cancellation):
            raise FullBuildCancelled
        return LogicalTerminologyFragment(
            component_id,
            assembly.evidence,
            extraction.candidates,
            assembly.excluded_reasons,
            tuple(sorted((*assembly.diagnostics, *extraction.diagnostics))),
            extraction.llm_status,
        )

    def freeze_fragments(
        self,
        snapshot: BuildInputSnapshot,
        fragments: tuple[LogicalTerminologyFragment, ...],
        source_records: tuple[SourceBuildRecord, ...],
        *,
        baseline_decisions: tuple[TermDecision, ...] = (),
    ) -> FullBuildOutcome:
        global_result = reduce_fragments(
            project_id=snapshot.project_id,
            variant_id=snapshot.variant_id,
            fragments=fragments,
            baseline_decisions=baseline_decisions,
            reducer=self._reducer,
            reconciler=self._reconciler,
        )
        diagnostics = {diagnostic for record in source_records for diagnostic in record.diagnostics} | set(
            global_result.diagnostics
        )
        partial = any(record.status is not SourceBuildStatus.COMPLETED for record in source_records) or any(
            item.startswith((
                "RELATION_SOURCE_MISSING",
                "RELATION_POLICY_UNSUPPORTED",
                "VARIANT_FINGERPRINT_MISMATCH",
            ))
            for item in global_result.diagnostics
        )
        completeness = BuildCompleteness.PARTIAL if partial else BuildCompleteness.FULL
        summary = BuildSummary(
            source_count=len(snapshot.sources),
            evidence_count=len(global_result.evidence),
            candidate_count=len(global_result.candidates),
            conflict_count=len(global_result.conflicts),
            excluded_count=sum(count for _, count in global_result.excluded_reasons),
        )
        stable_diagnostics = tuple(sorted(set(diagnostics)))
        semantic_result = {
            "schema": "terminology.build-result.v1",
            "project_id": snapshot.project_id,
            "variant_id": snapshot.variant_id,
            "summary": summary,
            "evidence": global_result.evidence,
            "candidates": global_result.candidates,
            "conflicts": global_result.conflicts,
            "excluded_reasons": global_result.excluded_reasons,
            "diagnostics": stable_diagnostics,
            "completeness": completeness,
            "llm_status": global_result.llm_status,
            "review_term_ids": global_result.reconciliation.review_term_ids,
        }
        content_digest = canonical_digest(semantic_result, namespace="terminology.build-result-content.v1")
        ref = BuildResultRef(build_key(snapshot), content_digest)
        result = BuildResult(
            ref=ref,
            project_id=snapshot.project_id,
            variant_id=snapshot.variant_id,
            summary=summary,
            evidence=global_result.evidence,
            candidates=global_result.candidates,
            conflicts=global_result.conflicts,
            excluded_reasons=global_result.excluded_reasons,
            diagnostics=stable_diagnostics,
            completeness=completeness,
            llm_status=global_result.llm_status,
        )
        self._repository.put_build(result)
        return FullBuildOutcome(
            result,
            tuple(sorted(source_records, key=lambda item: item.source_id)),
            global_result.reconciliation.effective_candidates,
            global_result.reconciliation.review_term_ids,
        )

    def parse_source(
        self,
        source: CapturedSource,
        context: RequestContext,
    ) -> tuple[SourceCorpusFragment | None, SourceBuildRecord]:
        started = monotonic()
        try:
            result = self._parser.parse(source, context)
        except Exception as exc:  # noqa: BLE001 - registered adapter boundary
            return None, _source_record(
                source,
                SourceBuildStatus.FAILED,
                0,
                monotonic() - started,
                (f"SOURCE_PARSE_FAILED:{source.registration.source_id}:{type(exc).__name__}",),
            )
        duration = monotonic() - started
        result_diagnostics = tuple(
            f"SOURCE_DIAGNOSTIC:{source.registration.source_id}:{item.code}" for item in result.diagnostics
        )
        if result.outcome is OperationOutcome.CANCELLED:
            return None, _source_record(source, SourceBuildStatus.CANCELLED, 0, duration, result_diagnostics)
        if result.outcome is OperationOutcome.FAILED or result.source_snapshot is None:
            return None, _source_record(source, SourceBuildStatus.FAILED, 0, duration, result_diagnostics)
        if result.format_id is not source.registration.format_id:
            return None, _source_record(
                source,
                SourceBuildStatus.FAILED,
                0,
                duration,
                (*result_diagnostics, f"SOURCE_FORMAT_MISMATCH:{source.registration.source_id}"),
            )
        if result.adapter_id is not None and result.adapter_id != source.adapter_id:
            return None, _source_record(
                source,
                SourceBuildStatus.FAILED,
                0,
                duration,
                (*result_diagnostics, f"SOURCE_ADAPTER_MISMATCH:{source.registration.source_id}"),
            )
        if result.adapter_version is not None and result.adapter_version != source.adapter_version:
            return None, _source_record(
                source,
                SourceBuildStatus.FAILED,
                0,
                duration,
                (*result_diagnostics, f"SOURCE_ADAPTER_VERSION_MISMATCH:{source.registration.source_id}"),
            )
        if result.source_snapshot.sha256 != source.lease.actual_fingerprint:
            return None, _source_record(
                source,
                SourceBuildStatus.FAILED,
                0,
                duration,
                (*result_diagnostics, f"SOURCE_FINGERPRINT_MISMATCH:{source.registration.source_id}"),
            )
        try:
            fragment = SourceCorpusFragment.from_parsed(
                source_id=source.registration.source_id,
                format_id=source.registration.format_id.value,
                fingerprint=source.lease.actual_fingerprint,
                entries=result.entries,
                plugin_scope=source.registration.plugin_scope,
            )
        except (TypeError, ValueError) as exc:
            return None, _source_record(
                source,
                SourceBuildStatus.FAILED,
                0,
                duration,
                (*result_diagnostics, f"SOURCE_ENTRY_INVALID:{source.registration.source_id}:{type(exc).__name__}"),
            )
        status = (
            SourceBuildStatus.PARTIAL if result.outcome is OperationOutcome.PARTIAL else SourceBuildStatus.COMPLETED
        )
        return fragment, _source_record(
            source,
            status,
            len(fragment.entries),
            duration,
            result_diagnostics,
        )


def _source_record(
    source: CapturedSource,
    status: SourceBuildStatus,
    entry_count: int,
    duration: float,
    diagnostics: tuple[str, ...],
) -> SourceBuildRecord:
    return SourceBuildRecord(
        source.registration.source_id,
        source.registration.format_id.value,
        source.adapter_id,
        source.adapter_version,
        status,
        entry_count,
        duration,
        tuple(sorted(diagnostics)),
    )


def _cancelled(cancellation: CancellationPort | None) -> bool:
    return cancellation is not None and cancellation.is_cancelled


__all__ = [
    "FullBuildCancelled",
    "FullBuildError",
    "FullBuildOutcome",
    "RegisteredSourceParserPort",
    "SourceBuildRecord",
    "SourceBuildStatus",
    "TerminologyFullBuilder",
    "TranslationIoRegisteredSourceParser",
]
