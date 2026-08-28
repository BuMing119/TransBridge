"""Deterministic and optional LLM terminology extraction over frozen evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from transbridge.converter.context_categories import AUTO_TERM_CONTEXTS

from .identity import candidate_id, normalize_original, normalize_translation
from .models import (
    BilingualEvidence,
    ExtractionMethod,
    LlmExtractionStatus,
    TermCandidate,
    TermScope,
)

DETERMINISTIC_ALGORITHM_VERSION = "terminology.deterministic-name.v1"
LLM_ALGORITHM_VERSION = "terminology.llm-text.v1"


@dataclass(frozen=True, slots=True)
class LlmEvidenceInput:
    evidence_id: str
    original: str
    translation: str
    context: str


@dataclass(frozen=True, slots=True)
class LlmTermProposal:
    evidence_id: str
    original: str
    translation: str


class LlmTerminologyExtractorPort(Protocol):
    def extract(self, batch: tuple[LlmEvidenceInput, ...]) -> tuple[LlmTermProposal, ...]: ...


class CancellationPort(Protocol):
    @property
    def is_cancelled(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    candidates: tuple[TermCandidate, ...]
    llm_status: LlmExtractionStatus
    diagnostics: tuple[str, ...] = ()
    cancelled: bool = False


class DeterministicTermExtractor:
    def extract(self, evidence: tuple[BilingualEvidence, ...]) -> tuple[TermCandidate, ...]:
        candidates = [
            _candidate(
                item,
                item.original,
                item.translation,
                ExtractionMethod.DETERMINISTIC_NAME,
                DETERMINISTIC_ALGORITHM_VERSION,
            )
            for item in evidence
            if _context_base(item.context) in AUTO_TERM_CONTEXTS
        ]
        return tuple(sorted(candidates, key=lambda item: item.candidate_id))


class TerminologyExtractionService:
    def __init__(
        self,
        deterministic: DeterministicTermExtractor | None = None,
        llm: LlmTerminologyExtractorPort | None = None,
        *,
        batch_size: int = 50,
    ) -> None:
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
            raise ValueError("LLM terminology batch size must be positive")
        self._deterministic = deterministic or DeterministicTermExtractor()
        self._llm = llm
        self._batch_size = batch_size

    def extract(
        self,
        evidence: tuple[BilingualEvidence, ...],
        *,
        llm_enabled: bool,
        cancellation: CancellationPort | None = None,
    ) -> ExtractionResult:
        ordered = tuple(sorted(evidence, key=lambda item: item.evidence_id))
        direct = self._deterministic.extract(ordered)
        if not llm_enabled:
            return ExtractionResult(direct, LlmExtractionStatus.SKIPPED, ("LLM_DISABLED",))
        if self._llm is None:
            return ExtractionResult(direct, LlmExtractionStatus.UNAVAILABLE, ("LLM_UNAVAILABLE",))

        text_evidence = tuple(item for item in ordered if _context_base(item.context) not in AUTO_TERM_CONTEXTS)
        if not text_evidence:
            return ExtractionResult(direct, LlmExtractionStatus.SKIPPED)
        evidence_by_id = {item.evidence_id: item for item in text_evidence}
        accepted: list[TermCandidate] = []
        diagnostics: list[str] = []
        failed = False
        for offset in range(0, len(text_evidence), self._batch_size):
            if _cancelled(cancellation):
                return ExtractionResult(direct, LlmExtractionStatus.PARTIAL, tuple(diagnostics), True)
            batch_evidence = text_evidence[offset : offset + self._batch_size]
            payload = tuple(
                LlmEvidenceInput(item.evidence_id, item.original, item.translation, item.context)
                for item in batch_evidence
            )
            try:
                proposals = self._llm.extract(payload)
            except Exception as exc:  # noqa: BLE001 - provider adapter boundary
                failed = True
                diagnostics.append(f"LLM_BATCH_FAILED:{type(exc).__name__}")
                continue
            if _cancelled(cancellation):
                diagnostics.append("LLM_LATE_RESULT_DISCARDED")
                return ExtractionResult(direct, LlmExtractionStatus.PARTIAL, tuple(diagnostics), True)
            for proposal in proposals:
                proposal_evidence_id = getattr(proposal, "evidence_id", "")
                source = evidence_by_id.get(proposal_evidence_id)
                if source is None:
                    diagnostics.append(f"LLM_EVIDENCE_UNKNOWN:{proposal_evidence_id}")
                    continue
                try:
                    located = _located_in_same_evidence(proposal, source)
                    if not proposal.original.strip() or not proposal.translation.strip():
                        raise ValueError("proposal terms must not be empty")
                except (AttributeError, TypeError, ValueError):
                    diagnostics.append(f"LLM_PROPOSAL_INVALID:{proposal_evidence_id}")
                    continue
                if not located:
                    diagnostics.append(f"LLM_PROPOSAL_NOT_LOCATED:{proposal_evidence_id}")
                    continue
                accepted.append(
                    _candidate(
                        source,
                        proposal.original,
                        proposal.translation,
                        ExtractionMethod.LLM_TEXT,
                        LLM_ALGORITHM_VERSION,
                    )
                )
        status = LlmExtractionStatus.PARTIAL if failed else LlmExtractionStatus.PERFORMED
        combined = {item.candidate_id: item for item in (*direct, *accepted)}
        return ExtractionResult(
            tuple(sorted(combined.values(), key=lambda item: item.candidate_id)),
            status,
            tuple(sorted(diagnostics)),
        )


def _candidate(
    evidence: BilingualEvidence,
    original: str,
    translation: str,
    method: ExtractionMethod,
    algorithm_version: str,
) -> TermCandidate:
    scope = TermScope.plugin(evidence.plugin_scope) if evidence.plugin_scope else TermScope.project()
    identity = candidate_id(
        evidence_ids=(evidence.evidence_id,),
        original=original,
        translation=translation,
        scope=scope,
        extraction_method=method,
        algorithm_version=algorithm_version,
    )
    return TermCandidate(
        identity,
        original.strip(),
        translation.strip(),
        normalize_original(original),
        normalize_translation(translation),
        (evidence.evidence_id,),
        scope,
        method,
        algorithm_version,
    )


def _located_in_same_evidence(proposal: LlmTermProposal, evidence: BilingualEvidence) -> bool:
    source = normalize_original(evidence.original)
    target = normalize_translation(evidence.translation)
    return normalize_original(proposal.original) in source and normalize_translation(proposal.translation) in target


def _context_base(value: str) -> str:
    return value.split("|", 1)[0]


def _cancelled(cancellation: CancellationPort | None) -> bool:
    return cancellation is not None and cancellation.is_cancelled


__all__ = [
    "CancellationPort",
    "DETERMINISTIC_ALGORITHM_VERSION",
    "DeterministicTermExtractor",
    "ExtractionResult",
    "LLM_ALGORITHM_VERSION",
    "LlmEvidenceInput",
    "LlmTermProposal",
    "LlmTerminologyExtractorPort",
    "TerminologyExtractionService",
]
