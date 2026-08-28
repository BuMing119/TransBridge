from __future__ import annotations

from dataclasses import dataclass
import hashlib

import pytest

from transbridge.application.contracts import Diagnostic, OperationOutcome, RequestContext
from transbridge.application.io import (
    CapabilityLevel,
    FormatCapability,
    FormatCapabilitySnapshot,
    FormatId,
    ParseResult,
    ParseStats,
    SourceDescriptor,
    SourceSnapshot,
)
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projects.source_registry import (
    BilingualCapability,
    SourceKind,
    SourceRegistration,
)
from transbridge.application.terminology.build import (
    FullBuildCancelled,
    FullBuildError,
    SourceBuildStatus,
    TerminologyFullBuilder,
)
from transbridge.application.terminology.in_memory import InMemoryTerminologyRepository
from transbridge.application.terminology.input_capture import BuildInputSnapshot, CapturedSource, SourceLease
from transbridge.application.terminology.models import BuildCompleteness
from transbridge.converter.translation_entry import STAGE_TRANSLATED, TranslationEntry
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef
from transbridge.persistence.v2.variant import VariantSnapshot

_EMPTY_DIGEST = hashlib.sha256(b"").hexdigest()


def _source(source_id: str) -> CapturedSource:
    content = f"content:{source_id}".encode()
    snapshot = SourceSnapshot.from_bytes(
        SourceDescriptor(f"memory://{source_id}.json", display_name=f"{source_id}.json"),
        FormatId.JSON_TRANSBRIDGE,
        content,
    )
    registration = SourceRegistration(
        source_id,
        True,
        FormatId.JSON_TRANSBRIDGE,
        f"memory://{source_id}.json",
        SourceKind.BILINGUAL,
        BilingualCapability.SELF_CONTAINED,
    )
    capability = FormatCapability(read=CapabilityLevel.SUPPORTED)
    return CapturedSource(
        registration,
        SourceLease(source_id, snapshot, snapshot.sha256),
        "adapter.test",
        "1",
        FormatCapabilitySnapshot(
            FormatId.JSON_TRANSBRIDGE,
            capability,
            capability,
            "adapter.test",
            "1",
        ),
        (),
    )


def _entry(source_id: str, key: str, original: str, translation: str) -> TranslationEntry:
    entry_key = EntryKey(SourceNamespace(f"project-source:{source_id}"), key)
    return TranslationEntry(
        key,
        key,
        original,
        translation,
        STAGE_TRANSLATED,
        "NPC_:FULL",
        entry_key=entry_key,
    )


def _snapshot(sources: tuple[CapturedSource, ...]) -> BuildInputSnapshot:
    variant = VariantSnapshot(
        VariantRef(VariantId("main"), ProjectId("project-1")),
        (),
        (),
        revision=3,
    )
    return BuildInputSnapshot(
        project_id="project-1",
        project_revision=5,
        variant_id="main",
        variant_revision=3,
        variant_snapshot=variant,
        variant_content_digest=_EMPTY_DIGEST,
        sources=sources,
        relations=(),
        config_digest=_EMPTY_DIGEST,
        effective_version_id=None,
        draft_id="no-draft",
        draft_base_version_id=None,
        draft_revision=0,
        decision_digest=_EMPTY_DIGEST,
    )


@dataclass
class _Parser:
    entries: dict[str, tuple[TranslationEntry, ...] | None]

    def parse(self, source: CapturedSource, context: RequestContext) -> ParseResult:
        values = self.entries[source.registration.source_id]
        if values is None:
            return ParseResult(
                OperationOutcome.FAILED,
                source.registration.format_id,
                source.lease.snapshot.source,
                diagnostics=(Diagnostic("TEST_PARSE_FAILED", "test source failed"),),
                stats=ParseStats(failed=1),
                adapter_id="adapter.test",
                adapter_version="1",
            )
        return ParseResult.completed(
            source.registration.format_id,
            source.lease.snapshot.source,
            source.lease.snapshot,
            values,
            adapter_id="adapter.test",
            adapter_version="1",
        )


def test_full_build_freezes_stable_result_independent_of_source_input_order() -> None:
    first = _source("source-a")
    second = _source("source-b")
    parser = _Parser({
        "source-a": (_entry("source-a", "one", "Dragon", "龙"),),
        "source-b": (_entry("source-b", "two", "Sword", "剑"),),
    })
    context = RequestContext("test", project_id="project-1", variant_id="main", run_id="run-1")

    outcome_a = TerminologyFullBuilder(parser, InMemoryTerminologyRepository()).build(
        _snapshot((first, second)), context
    )
    outcome_b = TerminologyFullBuilder(parser, InMemoryTerminologyRepository()).build(
        _snapshot((second, first)), context
    )

    assert outcome_a.result == outcome_b.result
    assert outcome_a.result.summary.evidence_count == 2
    assert outcome_a.result.summary.candidate_count == 2
    assert outcome_a.result.completeness is BuildCompleteness.FULL
    assert [record.source_id for record in outcome_a.source_records] == ["source-a", "source-b"]


def test_same_original_with_multiple_translations_is_never_silently_resolved() -> None:
    first = _source("source-a")
    second = _source("source-b")
    parser = _Parser({
        "source-a": (_entry("source-a", "one", "Dragon", "龙"),),
        "source-b": (_entry("source-b", "two", "Dragon", "巨龙"),),
    })

    outcome = TerminologyFullBuilder(parser, InMemoryTerminologyRepository()).build(
        _snapshot((first, second)), RequestContext("test", run_id="run-1")
    )

    assert outcome.result.summary.conflict_count == 1
    assert len(outcome.result.conflicts[0].variants) == 2
    assert outcome.result.conflicts[0].recommended_translation is None
    assert outcome.effective_candidates == ()


def test_single_source_failure_yields_partial_result_and_preserves_successes() -> None:
    good = _source("good")
    bad = _source("bad")
    parser = _Parser({
        "good": (_entry("good", "one", "Dragon", "龙"),),
        "bad": None,
    })

    outcome = TerminologyFullBuilder(parser, InMemoryTerminologyRepository()).build(
        _snapshot((bad, good)), RequestContext("test", run_id="run-1")
    )

    assert outcome.result.completeness is BuildCompleteness.PARTIAL
    assert outcome.result.summary.evidence_count == 1
    assert {item.status for item in outcome.source_records} == {
        SourceBuildStatus.COMPLETED,
        SourceBuildStatus.FAILED,
    }
    assert "SOURCE_DIAGNOSTIC:bad:TEST_PARSE_FAILED" in outcome.result.diagnostics


def test_all_source_failures_do_not_freeze_a_build() -> None:
    source = _source("bad")
    repository = InMemoryTerminologyRepository()

    with pytest.raises(FullBuildError) as exc_info:
        TerminologyFullBuilder(_Parser({"bad": None}), repository).build(
            _snapshot((source,)), RequestContext("test", run_id="run-1")
        )

    assert exc_info.value.code == "TERMINOLOGY_ALL_SOURCES_FAILED"


class _Cancelled:
    is_cancelled = True


def test_cancelled_build_does_not_commit_result() -> None:
    source = _source("source-a")
    parser = _Parser({"source-a": (_entry("source-a", "one", "Dragon", "龙"),)})

    with pytest.raises(FullBuildCancelled):
        TerminologyFullBuilder(parser, InMemoryTerminologyRepository()).build(
            _snapshot((source,)),
            RequestContext("test", run_id="run-1"),
            cancellation=_Cancelled(),
        )
