"""Canonical, loss-aware mappings used by terminology synchronization."""

from __future__ import annotations

from transbridge.ai_translator.term_formats import TermEntry
from transbridge.application.terminology.identity import normalize_original
from transbridge.application.terminology.models import ScopeKind, TermDecision

from .plan_models import TerminologyContentSummary, TerminologySyncReason


def local_content(decision: TermDecision) -> TerminologyContentSummary:
    """Project decisions map only to fields ParaTranz can faithfully represent."""

    return TerminologyContentSummary(
        original=decision.original,
        normalized_original=decision.normalized_original,
        translation=decision.translation,
        scope=decision.scope.canonical_key,
        suppressed=decision.suppressed,
        variants=decision.variants,
        note=decision.notes,
    )


def remote_content(entry: TermEntry) -> TerminologyContentSummary:
    return TerminologyContentSummary(
        original=entry.term,
        normalized_original=normalize_original(entry.term),
        translation=entry.translation,
        scope="project",
        variants=tuple(entry.variants),
        case_sensitive=entry.case_sensitive,
        part_of_speech=entry.pos,
        note=entry.note,
    )


def lossy_mapping_reason(decision: TermDecision) -> TerminologySyncReason | None:
    if decision.scope.kind is ScopeKind.PLUGIN:
        return TerminologySyncReason.PLUGIN_SCOPE
    if decision.suppressed:
        return TerminologySyncReason.SUPPRESSION_NOT_REPRESENTABLE
    if decision.replacement_of is not None:
        return TerminologySyncReason.REPLACEMENT_NOT_REPRESENTABLE
    return None


def content_equal(left: TerminologyContentSummary | None, right: TerminologyContentSummary | None) -> bool:
    return left is not None and right is not None and left.digest == right.digest


__all__ = ["content_equal", "local_content", "lossy_mapping_reason", "remote_content"]
