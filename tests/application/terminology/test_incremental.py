from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib

from transbridge.application.contracts import RequestContext
from transbridge.application.io import (
    CapabilityLevel,
    FormatCapability,
    FormatCapabilitySnapshot,
    FormatId,
    ParseResult,
    SourceDescriptor,
    SourceSnapshot,
)
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projects.source_registry import (
    BilingualCapability,
    SourceKind,
    SourceRegistration,
    SourceRelation,
    SourceRelationKind,
)
from transbridge.application.terminology.build import TerminologyFullBuilder
from transbridge.application.terminology.extraction import (
    LlmEvidenceInput,
    LlmTermProposal,
    TerminologyExtractionService,
)
from transbridge.application.terminology.identity import build_key
from transbridge.application.terminology.in_memory import InMemoryTerminologyRepository
from transbridge.application.terminology.incremental import (
    BuildReuseDecision,
    IncrementalBuildPlanner,
    IncrementalTerminologyBuilder,
    parse_content_key,
)
from transbridge.application.terminology.input_capture import BuildInputSnapshot, CapturedSource, SourceLease
from transbridge.converter.translation_entry import STAGE_TRANSLATED, TranslationEntry
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef
from transbridge.persistence.v2.variant import SourceFingerprint, VariantEntryState, VariantSnapshot

_EMPTY_DIGEST = hashlib.sha256(b"").hexdigest()


def _source(source_id: str, content: bytes | None = None, *, adapter_version: str = "1") -> CapturedSource:
    content = content or source_id.encode()
    source_snapshot = SourceSnapshot.from_bytes(
        SourceDescriptor(f"memory://{source_id}.json"), FormatId.JSON_TRANSBRIDGE, content
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
        SourceLease(source_id, source_snapshot, source_snapshot.sha256),
        "adapter.test",
        adapter_version,
        FormatCapabilitySnapshot(FormatId.JSON_TRANSBRIDGE, capability, capability, "adapter.test", adapter_version),
        (),
    )


def _snapshot(sources: tuple[CapturedSource, ...], relations: tuple[SourceRelation, ...] = ()) -> BuildInputSnapshot:
    variant = VariantSnapshot(VariantRef(VariantId("main"), ProjectId("project-1")), (), (), revision=3)
    return BuildInputSnapshot(
        "project-1",
        5,
        "main",
        3,
        variant,
        _EMPTY_DIGEST,
        sources,
        relations,
        _EMPTY_DIGEST,
        None,
        "no-draft",
        None,
        0,
        _EMPTY_DIGEST,
    )


def _entry(source_id: str, original: str, translation: str, context: str = "NPC_:FULL") -> TranslationEntry:
    key = EntryKey(SourceNamespace(f"project-source:{source_id}"), "one")
    return TranslationEntry("one", "one", original, translation, STAGE_TRANSLATED, context, entry_key=key)


@dataclass
class _Parser:
    values: dict[str, tuple[TranslationEntry, ...]]
    calls: int = 0

    def parse(self, source: CapturedSource, context: RequestContext) -> ParseResult:
        self.calls += 1
        return ParseResult.completed(
            source.registration.format_id,
            source.lease.snapshot.source,
            source.lease.snapshot,
            self.values[source.registration.source_id],
            adapter_id=source.adapter_id,
            adapter_version=source.adapter_version,
        )


@dataclass(frozen=True)
class _Entry:
    payload: bytes


class _Cache:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], bytes] = {}

    def get(self, kind: str, key: str) -> _Entry | None:
        payload = self.values.get((str(kind), key))
        return None if payload is None else _Entry(payload)

    def put(self, kind: str, key: str, payload: bytes) -> _Entry:
        self.values[(str(kind), key)] = payload
        return _Entry(payload)


@dataclass
class _Llm:
    calls: int = 0

    def extract(self, batch: tuple[LlmEvidenceInput, ...]) -> tuple[LlmTermProposal, ...]:
        self.calls += 1
        return tuple(LlmTermProposal(item.evidence_id, "Dragon", "龙") for item in batch)


def test_parse_key_and_component_key_have_explicit_invalidation_boundaries() -> None:
    first = _source("a", b"one")
    changed_content = _source("a", b"two")
    changed_adapter = _source("a", b"one", adapter_version="2")
    assert parse_content_key(first) != parse_content_key(changed_content)
    assert parse_content_key(first) != parse_content_key(changed_adapter)

    snapshot = _snapshot((first, _source("b")))
    draft_changed = replace(snapshot, draft_id="draft-2", draft_base_version_id="v1", draft_revision=2)
    planner = IncrementalBuildPlanner()
    assert tuple(item.digest for item in planner.components(snapshot)) == tuple(
        item.digest for item in planner.components(draft_changed)
    )
    assert build_key(snapshot) != build_key(draft_changed)


def test_relations_form_undirected_components_and_policy_changes_extraction_key() -> None:
    sources = (_source("a"), _source("b"), _source("c"))
    relation = SourceRelation("r1", SourceRelationKind.TRANSLATION_FOR, "a", "b")
    planner = IncrementalBuildPlanner()
    components = planner.components(_snapshot(sources, (relation,)))
    assert sorted(item.source_ids for item in components) == [("a", "b"), ("c",)]

    changed = replace(relation, alignment_version="2")
    changed_components = planner.components(_snapshot(sources, (changed,)))
    assert next(item.digest for item in components if item.source_ids == ("a", "b")) != next(
        item.digest for item in changed_components if item.source_ids == ("a", "b")
    )


def test_component_key_uses_llm_mode_and_only_relevant_variant_content() -> None:
    sources = (_source("a"), _source("b"), _source("c"))
    snapshot = _snapshot(sources)
    planner = IncrementalBuildPlanner()

    disabled = planner.components(snapshot, llm_enabled=False)
    enabled = planner.components(snapshot, llm_enabled=True)
    assert all(left.digest != right.digest for left, right in zip(disabled, enabled, strict=True))

    global_digest_only = replace(snapshot, variant_content_digest=hashlib.sha256(b"global-only").hexdigest())
    assert [item.digest for item in planner.components(snapshot)] == [
        item.digest for item in planner.components(global_digest_only)
    ]

    namespace = SourceNamespace("project-source:a")
    key = EntryKey(namespace, "one")
    fingerprint = sources[0].lease.actual_fingerprint
    base_variant = VariantSnapshot(
        snapshot.variant_snapshot.ref,
        (SourceFingerprint(namespace, fingerprint),),
        (VariantEntryState(key, "old", STAGE_TRANSLATED),),
        revision=4,
    )
    changed_variant = replace(
        base_variant,
        entries=(VariantEntryState(key, "new", STAGE_TRANSLATED),),
        revision=5,
    )
    base = replace(
        snapshot,
        variant_revision=4,
        variant_snapshot=base_variant,
        variant_content_digest=hashlib.sha256(b"base variant").hexdigest(),
    )
    changed = replace(
        snapshot,
        variant_revision=5,
        variant_snapshot=changed_variant,
        variant_content_digest=hashlib.sha256(b"changed variant").hexdigest(),
    )
    base_digests = {item.source_ids: item.digest for item in planner.components(base)}
    changed_digests = {item.source_ids: item.digest for item in planner.components(changed)}

    assert base_digests[("a",)] != changed_digests[("a",)]
    assert base_digests[("b",)] == changed_digests[("b",)]
    assert base_digests[("c",)] == changed_digests[("c",)]


def test_exact_hit_skips_parse_and_corrupt_component_falls_back_to_full() -> None:
    source = _source("a")
    snapshot = _snapshot((source,))
    parser = _Parser({"a": (_entry("a", "Dragon", "龙"),)})
    repository = InMemoryTerminologyRepository()
    cache = _Cache()
    builder = IncrementalTerminologyBuilder(TerminologyFullBuilder(parser, repository), repository, cache)
    context = RequestContext("test", run_id="run")

    cold = builder.build(snapshot, context)
    assert cold.plan.decision is BuildReuseDecision.FULL
    assert parser.calls == 1
    exact = builder.build(snapshot, context)
    assert exact.plan.decision is BuildReuseDecision.EXACT
    assert parser.calls == 1

    changed_draft = replace(snapshot, draft_id="draft-2", draft_revision=1)
    component = IncrementalBuildPlanner().components(changed_draft)[0]
    cache.values[("extraction", component.digest)] = b"not-json"
    fallback = builder.build(changed_draft, context)
    assert fallback.plan.decision is BuildReuseDecision.FULL_FALLBACK
    assert parser.calls == 2
    assert fallback.result.ref.content_digest == cold.result.ref.content_digest


def test_exact_hit_skips_llm_and_config_change_reextracts_without_reparse() -> None:
    source = _source("a")
    snapshot = _snapshot((source,))
    parser = _Parser({"a": (_entry("a", "Dragon guards", "龙守卫", "BOOK:DESC"),)})
    llm = _Llm()
    repository = InMemoryTerminologyRepository()
    builder = IncrementalTerminologyBuilder(
        TerminologyFullBuilder(
            parser,
            repository,
            extraction=TerminologyExtractionService(llm=llm),
        ),
        repository,
        _Cache(),
    )
    context = RequestContext("test", run_id="run")

    builder.build(snapshot, context, llm_enabled=True)
    builder.build(snapshot, context, llm_enabled=True)
    assert (parser.calls, llm.calls) == (1, 1)

    config_changed = replace(snapshot, config_digest=hashlib.sha256(b"new config").hexdigest())
    changed = builder.build(config_changed, context, llm_enabled=True)
    assert changed.plan.reextracted_component_ids
    assert (parser.calls, llm.calls) == (1, 2)


def test_switching_llm_mode_misses_exact_and_component_cache_without_reparse() -> None:
    source = _source("a")
    snapshot = _snapshot((source,))
    parser = _Parser({"a": (_entry("a", "Dragon guards", "龙守卫", "BOOK:DESC"),)})
    llm = _Llm()
    repository = InMemoryTerminologyRepository()
    builder = IncrementalTerminologyBuilder(
        TerminologyFullBuilder(
            parser,
            repository,
            extraction=TerminologyExtractionService(llm=llm),
        ),
        repository,
        _Cache(),
    )
    context = RequestContext("test", run_id="run")

    disabled = builder.build(snapshot, context, llm_enabled=False)
    enabled = builder.build(snapshot, context, llm_enabled=True)
    exact_enabled = builder.build(snapshot, context, llm_enabled=True)

    assert disabled.plan.build_key != enabled.plan.build_key
    assert enabled.plan.decision is BuildReuseDecision.INCREMENTAL
    assert enabled.plan.reextracted_component_ids
    assert exact_enabled.plan.decision is BuildReuseDecision.EXACT
    assert (parser.calls, llm.calls) == (1, 1)
