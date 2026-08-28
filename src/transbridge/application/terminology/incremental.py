"""Content-keyed incremental terminology builds with full-result equivalence."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib
import json
from typing import Any, Protocol

from transbridge.application.contracts import RequestContext
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.io.stage_policy import Stage

from .build import (
    FullBuildError,
    SourceBuildRecord,
    SourceBuildStatus,
    TerminologyFullBuilder,
)
from .corpus import CorpusEntry, SourceCorpusFragment
from .extraction import CancellationPort
from .identity import (
    BUILD_KEY_SCHEMA,
    NORMALIZATION_SCHEMA,
    build_key,
    canonical_digest,
)
from .input_capture import BuildInputSnapshot, CapturedSource
from .models import (
    BilingualEvidence,
    BuildResult,
    BuildResultRef,
    ExtractionMethod,
    LlmExtractionStatus,
    TermCandidate,
    TermDecision,
    TermScope,
)
from .ports import TerminologyRepositoryPort
from .reducer import LogicalTerminologyFragment

PARSE_KEY_SCHEMA = "terminology.parse-key.v1"
EXTRACTION_KEY_SCHEMA = "terminology.extraction-key.v1"
RELATION_COMPONENT_SCHEMA = "terminology.relation-component.v1"
COMPONENT_VARIANT_SCHEMA = "terminology.component-variant.v1"
PARSE_CACHE_SCHEMA = "terminology.parse-cache.v1"
EXTRACTION_CACHE_SCHEMA = "terminology.extraction-cache.v1"
BUILD_CACHE_SCHEMA = "terminology.build-cache.v2"


class CacheEntryPort(Protocol):
    payload: bytes


class IncrementalCachePort(Protocol):
    def get(self, kind: str, key: str) -> CacheEntryPort | None: ...

    def put(self, kind: str, key: str, payload: bytes) -> CacheEntryPort: ...


class BuildReuseDecision(StrEnum):
    EXACT = "exact"
    INCREMENTAL = "incremental"
    FULL = "full"
    FULL_FALLBACK = "full_fallback"


@dataclass(frozen=True, slots=True)
class RelationComponentDigest:
    component_id: str
    source_ids: tuple[str, ...]
    relation_ids: tuple[str, ...]
    digest: str


@dataclass(frozen=True, slots=True)
class RecomputePlan:
    build_key: str
    decision: BuildReuseDecision
    components: tuple[RelationComponentDigest, ...]
    reused_component_ids: tuple[str, ...] = ()
    recomputed_component_ids: tuple[str, ...] = ()
    reused_source_ids: tuple[str, ...] = ()
    reparsed_source_ids: tuple[str, ...] = ()
    reassembled_component_ids: tuple[str, ...] = ()
    reextracted_component_ids: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()

    @property
    def reused_component_count(self) -> int:
        return len(self.reused_component_ids)

    @property
    def recomputed_component_count(self) -> int:
        return len(self.recomputed_component_ids)

    @property
    def reparsed_source_count(self) -> int:
        return len(self.reparsed_source_ids)


@dataclass(frozen=True, slots=True)
class IncrementalBuildOutcome:
    result: BuildResult
    plan: RecomputePlan
    source_records: tuple[SourceBuildRecord, ...]


class IncrementalBuildPlanner:
    def components(
        self,
        snapshot: BuildInputSnapshot,
        *,
        llm_enabled: bool = False,
    ) -> tuple[RelationComponentDigest, ...]:
        sources = {item.registration.source_id: item for item in snapshot.sources}
        graph = {source_id: set() for source_id in sources}
        for relation in snapshot.relations:
            if relation.from_source_id in graph and relation.to_source_id in graph:
                graph[relation.from_source_id].add(relation.to_source_id)
                graph[relation.to_source_id].add(relation.from_source_id)
        pending = set(graph)
        result: list[RelationComponentDigest] = []
        while pending:
            root = min(pending)
            stack = [root]
            members: set[str] = set()
            while stack:
                current = stack.pop()
                if current in members:
                    continue
                members.add(current)
                stack.extend(sorted(graph[current].difference(members), reverse=True))
            pending.difference_update(members)
            source_ids = tuple(sorted(members))
            relations = tuple(
                sorted(
                    (
                        relation
                        for relation in snapshot.relations
                        if relation.from_source_id in members and relation.to_source_id in members
                    ),
                    key=lambda item: item.relation_id,
                )
            )
            component_id = canonical_digest(source_ids, namespace=RELATION_COMPONENT_SCHEMA)
            digest = canonical_digest(
                {
                    "schema": EXTRACTION_KEY_SCHEMA,
                    "component_id": component_id,
                    "project_id": snapshot.project_id,
                    "variant_id": snapshot.variant_id,
                    "parse_keys": [parse_content_key(sources[source_id]) for source_id in source_ids],
                    "relations": [item.to_dict() for item in relations],
                    "component_variant_digest": _component_variant_digest(snapshot, source_ids),
                    "config_digest": snapshot.config_digest,
                    "llm_enabled": llm_enabled,
                    "normalization_schema": NORMALIZATION_SCHEMA,
                    "eligibility_schema": "terminology.eligibility.v1",
                    "deterministic_extractor": "terminology.deterministic-name.v1",
                    "llm_extractor": "terminology.llm-text.v1",
                },
                namespace=EXTRACTION_KEY_SCHEMA,
            )
            result.append(
                RelationComponentDigest(
                    component_id,
                    source_ids,
                    tuple(item.relation_id for item in relations),
                    digest,
                )
            )
        return tuple(sorted(result, key=lambda item: item.component_id))

    def plan(
        self,
        snapshot: BuildInputSnapshot,
        *,
        reusable_component_digests: frozenset[str] = frozenset(),
        llm_enabled: bool = False,
    ) -> RecomputePlan:
        components = self.components(snapshot, llm_enabled=llm_enabled)
        reused = tuple(item.component_id for item in components if item.digest in reusable_component_digests)
        recomputed = tuple(item.component_id for item in components if item.digest not in reusable_component_digests)
        decision = BuildReuseDecision.INCREMENTAL if reused else BuildReuseDecision.FULL
        return RecomputePlan(
            _exact_build_key(snapshot, llm_enabled=llm_enabled),
            decision,
            components,
            reused_component_ids=reused,
            recomputed_component_ids=recomputed,
            reassembled_component_ids=recomputed,
            reextracted_component_ids=recomputed,
        )


class IncrementalTerminologyBuilder:
    def __init__(
        self,
        full_builder: TerminologyFullBuilder,
        repository: TerminologyRepositoryPort,
        cache: IncrementalCachePort,
        *,
        planner: IncrementalBuildPlanner | None = None,
    ) -> None:
        self._full_builder = full_builder
        self._repository = repository
        self._cache = cache
        self._planner = planner or IncrementalBuildPlanner()

    def build(
        self,
        snapshot: BuildInputSnapshot,
        context: RequestContext,
        *,
        baseline_decisions: tuple[TermDecision, ...] = (),
        llm_enabled: bool = False,
        cancellation: CancellationPort | None = None,
    ) -> IncrementalBuildOutcome:
        result_snapshot = _snapshot_with_llm_mode(snapshot, llm_enabled=llm_enabled)
        current_build_key = build_key(result_snapshot)
        components = self._planner.components(snapshot, llm_enabled=llm_enabled)
        try:
            exact = self._load_exact(current_build_key, snapshot)
        except Exception as exc:  # noqa: BLE001 - corrupted disposable cache is a full-build miss
            return self._fallback_full(
                snapshot,
                context,
                components,
                f"CACHE_CORRUPT:build:{type(exc).__name__}",
                baseline_decisions,
                llm_enabled,
                cancellation,
            )
        if exact is not None:
            source_ids = tuple(item.registration.source_id for item in snapshot.sources)
            plan = RecomputePlan(
                current_build_key,
                BuildReuseDecision.EXACT,
                components,
                reused_component_ids=tuple(item.component_id for item in components),
                reused_source_ids=source_ids,
            )
            return IncrementalBuildOutcome(exact, plan, ())

        source_map = {item.registration.source_id: item for item in snapshot.sources}
        relation_map = {item.relation_id: item for item in snapshot.relations}
        logical_by_component: dict[str, LogicalTerminologyFragment] = {}
        records_by_source: dict[str, SourceBuildRecord] = {}
        reused_components: list[str] = []
        recomputed_components: list[str] = []
        reused_sources: set[str] = set()
        reparsed_sources: set[str] = set()
        diagnostics: list[str] = []

        missing_components: list[RelationComponentDigest] = []
        try:
            for component in components:
                cached = self._cache.get("extraction", component.digest)
                if cached is None:
                    missing_components.append(component)
                    continue
                logical, cached_records = _decode_component_cache(cached.payload, component)
                logical_by_component[component.component_id] = logical
                reused_components.append(component.component_id)
                reused_sources.update(component.source_ids)
                records_by_source.update({item.source_id: item for item in cached_records})
        except Exception as exc:  # noqa: BLE001 - corrupted disposable cache is a full-build miss
            return self._fallback_full(
                snapshot,
                context,
                components,
                f"CACHE_CORRUPT:extraction:{type(exc).__name__}",
                baseline_decisions,
                llm_enabled,
                cancellation,
            )

        parsed_fragments: dict[str, SourceCorpusFragment] = {}
        try:
            for component in missing_components:
                for source_id in component.source_ids:
                    source = source_map[source_id]
                    key = parse_content_key(source)
                    cached = self._cache.get("parse", key)
                    if cached is None:
                        continue
                    fragment, record = _decode_parse_cache(cached.payload, key)
                    parsed_fragments[source_id] = fragment
                    records_by_source[source_id] = record
                    reused_sources.add(source_id)
        except Exception as exc:  # noqa: BLE001 - corrupted disposable cache is a full-build miss
            return self._fallback_full(
                snapshot,
                context,
                components,
                f"CACHE_CORRUPT:parse:{type(exc).__name__}",
                baseline_decisions,
                llm_enabled,
                cancellation,
            )

        any_readable = bool(parsed_fragments) or bool(logical_by_component)
        for component in missing_components:
            component_fragments: list[SourceCorpusFragment] = []
            component_records: list[SourceBuildRecord] = []
            for source_id in component.source_ids:
                fragment = parsed_fragments.get(source_id)
                record = records_by_source.get(source_id)
                if fragment is None or record is None:
                    fragment, record = self._full_builder.parse_source(source_map[source_id], context)
                    records_by_source[source_id] = record
                    reparsed_sources.add(source_id)
                    if fragment is not None and record.status is SourceBuildStatus.COMPLETED:
                        self._safe_cache_put(
                            "parse",
                            parse_content_key(source_map[source_id]),
                            _encode_parse_cache(parse_content_key(source_map[source_id]), fragment, record),
                            diagnostics,
                        )
                if fragment is not None:
                    any_readable = True
                    component_fragments.append(fragment)
                component_records.append(record)
            relations = tuple(relation_map[item] for item in component.relation_ids)
            logical = self._full_builder.build_logical_fragment(
                component_id=component.component_id,
                snapshot=snapshot,
                fragments=tuple(component_fragments),
                relations=relations,
                llm_enabled=llm_enabled,
                cancellation=cancellation,
            )
            logical_by_component[component.component_id] = logical
            recomputed_components.append(component.component_id)
            if all(item.status is SourceBuildStatus.COMPLETED for item in component_records):
                self._safe_cache_put(
                    "extraction",
                    component.digest,
                    _encode_component_cache(component, logical, tuple(component_records)),
                    diagnostics,
                )
        if not any_readable:
            raise FullBuildError("TERMINOLOGY_ALL_SOURCES_FAILED", "no registered terminology source was readable")

        outcome = self._full_builder.freeze_fragments(
            result_snapshot,
            tuple(logical_by_component[item.component_id] for item in components),
            tuple(records_by_source.values()),
            baseline_decisions=baseline_decisions,
        )
        self._store_exact(current_build_key, outcome.result, diagnostics)
        decision = BuildReuseDecision.INCREMENTAL if reused_components or reused_sources else BuildReuseDecision.FULL
        plan = RecomputePlan(
            current_build_key,
            decision,
            components,
            tuple(sorted(reused_components)),
            tuple(sorted(recomputed_components)),
            tuple(sorted(reused_sources)),
            tuple(sorted(reparsed_sources)),
            tuple(sorted(recomputed_components)),
            tuple(sorted(recomputed_components)),
            tuple(sorted(diagnostics)),
        )
        return IncrementalBuildOutcome(outcome.result, plan, outcome.source_records)

    def _load_exact(self, key: str, snapshot: BuildInputSnapshot) -> BuildResult | None:
        cached = self._cache.get("build", key)
        if cached is None:
            return None
        payload = _read_payload(cached.payload, BUILD_CACHE_SCHEMA)
        if payload["exact_build_key"] != key or payload["input_schema"] != BUILD_KEY_SCHEMA:
            raise ValueError("cached build baseline does not match the current input")
        ref = BuildResultRef(str(payload["result_build_key"]), str(payload["content_digest"]))
        result = self._repository.get_build(ref)
        if result.project_id != snapshot.project_id or result.variant_id != snapshot.variant_id:
            raise ValueError("cached build belongs to another Project/Variant line")
        return result

    def _store_exact(self, key: str, result: BuildResult, diagnostics: list[str]) -> None:
        payload = _json_bytes({
            "schema": BUILD_CACHE_SCHEMA,
            "input_schema": BUILD_KEY_SCHEMA,
            "exact_build_key": key,
            "result_build_key": result.ref.build_key,
            "content_digest": result.ref.content_digest,
        })
        self._safe_cache_put("build", key, payload, diagnostics)

    def _safe_cache_put(self, kind: str, key: str, payload: bytes, diagnostics: list[str]) -> None:
        try:
            self._cache.put(kind, key, payload)
        except Exception as exc:  # noqa: BLE001 - disposable cache must not alter business output
            diagnostics.append(f"CACHE_WRITE_FAILED:{kind}:{type(exc).__name__}")

    def _fallback_full(
        self,
        snapshot: BuildInputSnapshot,
        context: RequestContext,
        components: tuple[RelationComponentDigest, ...],
        diagnostic: str,
        baseline_decisions: tuple[TermDecision, ...],
        llm_enabled: bool,
        cancellation: CancellationPort | None,
    ) -> IncrementalBuildOutcome:
        result_snapshot = _snapshot_with_llm_mode(snapshot, llm_enabled=llm_enabled)
        outcome = self._full_builder.build(
            result_snapshot,
            context,
            baseline_decisions=baseline_decisions,
            llm_enabled=llm_enabled,
            cancellation=cancellation,
        )
        cache_diagnostics = [diagnostic]
        current_build_key = build_key(result_snapshot)
        self._store_exact(current_build_key, outcome.result, cache_diagnostics)
        component_ids = tuple(item.component_id for item in components)
        source_ids = tuple(item.registration.source_id for item in snapshot.sources)
        plan = RecomputePlan(
            current_build_key,
            BuildReuseDecision.FULL_FALLBACK,
            components,
            recomputed_component_ids=component_ids,
            reparsed_source_ids=source_ids,
            reassembled_component_ids=component_ids,
            reextracted_component_ids=component_ids,
            diagnostics=tuple(sorted(cache_diagnostics)),
        )
        return IncrementalBuildOutcome(outcome.result, plan, outcome.source_records)


def _exact_build_key(snapshot: BuildInputSnapshot, *, llm_enabled: bool) -> str:
    return build_key(_snapshot_with_llm_mode(snapshot, llm_enabled=llm_enabled))


def _snapshot_with_llm_mode(snapshot: BuildInputSnapshot, *, llm_enabled: bool) -> BuildInputSnapshot:
    if not llm_enabled:
        return snapshot
    mode_payload = f"{snapshot.config_digest}\0llm_enabled={int(llm_enabled)}".encode()
    return replace(snapshot, config_digest=hashlib.sha256(mode_payload).hexdigest())


def _component_variant_digest(snapshot: BuildInputSnapshot, source_ids: tuple[str, ...]) -> str:
    namespaces = {SourceNamespace(f"project-source:{source_id}") for source_id in source_ids}
    return canonical_digest(
        {
            "source_fingerprints": [
                item.to_dict() for item in snapshot.variant_snapshot.source_fingerprints if item.namespace in namespaces
            ],
            "entries": [
                item.to_dict() for item in snapshot.variant_snapshot.entries if item.entry_key.namespace in namespaces
            ],
        },
        namespace=COMPONENT_VARIANT_SCHEMA,
    )


def parse_content_key(source: CapturedSource) -> str:
    return canonical_digest(
        {
            "schema": PARSE_KEY_SCHEMA,
            "source_id": source.registration.source_id,
            "fingerprint": source.lease.actual_fingerprint,
            "format_id": source.registration.format_id.value,
            "adapter_id": source.adapter_id,
            "adapter_version": source.adapter_version,
            "parse_options": dict(source.parse_options),
        },
        namespace=PARSE_KEY_SCHEMA,
    )


def _encode_parse_cache(
    key: str,
    fragment: SourceCorpusFragment,
    record: SourceBuildRecord,
) -> bytes:
    return _json_bytes({
        "schema": PARSE_CACHE_SCHEMA,
        "key": key,
        "fragment": _fragment_to_dict(fragment),
        "record": _record_to_dict(record),
    })


def _decode_parse_cache(payload: bytes, key: str) -> tuple[SourceCorpusFragment, SourceBuildRecord]:
    value = _read_payload(payload, PARSE_CACHE_SCHEMA)
    if value["key"] != key:
        raise ValueError("parse cache key mismatch")
    return _fragment_from_dict(value["fragment"]), _record_from_dict(value["record"])


def _encode_component_cache(
    component: RelationComponentDigest,
    fragment: LogicalTerminologyFragment,
    records: tuple[SourceBuildRecord, ...],
) -> bytes:
    return _json_bytes({
        "schema": EXTRACTION_CACHE_SCHEMA,
        "component_digest": component.digest,
        "component_id": component.component_id,
        "source_ids": component.source_ids,
        "fragment": _logical_to_dict(fragment),
        "records": [_record_to_dict(item) for item in records],
    })


def _decode_component_cache(
    payload: bytes,
    component: RelationComponentDigest,
) -> tuple[LogicalTerminologyFragment, tuple[SourceBuildRecord, ...]]:
    value = _read_payload(payload, EXTRACTION_CACHE_SCHEMA)
    if value["component_digest"] != component.digest or value["component_id"] != component.component_id:
        raise ValueError("extraction component cache identity mismatch")
    if tuple(value["source_ids"]) != component.source_ids:
        raise ValueError("extraction component source set mismatch")
    fragment = _logical_from_dict(value["fragment"])
    records = tuple(_record_from_dict(item) for item in value["records"])
    if fragment.component_id != component.component_id:
        raise ValueError("logical fragment component identity mismatch")
    return fragment, records


def _fragment_to_dict(fragment: SourceCorpusFragment) -> dict[str, Any]:
    return {
        "source_id": fragment.source_id,
        "format_id": fragment.format_id,
        "fingerprint": fragment.fingerprint,
        "plugin_scope": fragment.plugin_scope,
        "entries": [
            {
                "entry_key": item.entry_key.to_dict(),
                "original": item.original,
                "translation": item.translation,
                "stage": item.stage.value,
                "context": item.context,
                "from_current_variant": item.from_current_variant,
            }
            for item in fragment.entries
        ],
    }


def _fragment_from_dict(value: dict[str, Any]) -> SourceCorpusFragment:
    return SourceCorpusFragment(
        str(value["source_id"]),
        str(value["format_id"]),
        str(value["fingerprint"]),
        tuple(
            CorpusEntry(
                EntryKey.from_dict(item["entry_key"]),
                str(item["original"]),
                str(item["translation"]),
                Stage(int(item["stage"])),
                str(item["context"]),
                bool(item["from_current_variant"]),
            )
            for item in value["entries"]
        ),
        None if value.get("plugin_scope") is None else str(value["plugin_scope"]),
    )


def _record_to_dict(record: SourceBuildRecord) -> dict[str, Any]:
    return {
        "source_id": record.source_id,
        "format_id": record.format_id,
        "adapter_id": record.adapter_id,
        "adapter_version": record.adapter_version,
        "status": record.status.value,
        "entry_count": record.entry_count,
        "diagnostics": record.diagnostics,
    }


def _record_from_dict(value: dict[str, Any]) -> SourceBuildRecord:
    return SourceBuildRecord(
        str(value["source_id"]),
        str(value["format_id"]),
        str(value["adapter_id"]),
        str(value["adapter_version"]),
        SourceBuildStatus(str(value["status"])),
        int(value["entry_count"]),
        0.0,
        tuple(str(item) for item in value["diagnostics"]),
    )


def _logical_to_dict(fragment: LogicalTerminologyFragment) -> dict[str, Any]:
    return {
        "component_id": fragment.component_id,
        "evidence": [_evidence_to_dict(item) for item in fragment.evidence],
        "candidates": [_candidate_to_dict(item) for item in fragment.candidates],
        "excluded_reasons": fragment.excluded_reasons,
        "diagnostics": fragment.diagnostics,
        "llm_status": fragment.llm_status.value,
    }


def _logical_from_dict(value: dict[str, Any]) -> LogicalTerminologyFragment:
    return LogicalTerminologyFragment(
        str(value["component_id"]),
        tuple(_evidence_from_dict(item) for item in value["evidence"]),
        tuple(_candidate_from_dict(item) for item in value["candidates"]),
        tuple((str(key), int(count)) for key, count in value["excluded_reasons"]),
        tuple(str(item) for item in value["diagnostics"]),
        LlmExtractionStatus(str(value["llm_status"])),
    )


def _evidence_to_dict(item: BilingualEvidence) -> dict[str, Any]:
    return {field: getattr(item, field) for field in item.__dataclass_fields__}


def _evidence_from_dict(value: dict[str, Any]) -> BilingualEvidence:
    return BilingualEvidence(
        evidence_id=str(value["evidence_id"]),
        project_id=str(value["project_id"]),
        variant_id=str(value["variant_id"]),
        source_chain=tuple(str(item) for item in value["source_chain"]),
        namespace=str(value["namespace"]),
        entry_key=str(value["entry_key"]),
        original=str(value["original"]),
        translation=str(value["translation"]),
        source_format=str(value["source_format"]),
        source_fingerprint=str(value["source_fingerprint"]),
        context=str(value["context"]),
        stage=str(value["stage"]),
        plugin_scope=None if value.get("plugin_scope") is None else str(value["plugin_scope"]),
        from_current_variant=bool(value["from_current_variant"]),
    )


def _candidate_to_dict(item: TermCandidate) -> dict[str, Any]:
    return {
        "candidate_id": item.candidate_id,
        "original": item.original,
        "translation": item.translation,
        "normalized_original": item.normalized_original,
        "normalized_translation": item.normalized_translation,
        "evidence_ids": item.evidence_ids,
        "scope": {"kind": item.scope.kind.value, "plugin_id": item.scope.plugin_id},
        "extraction_method": item.extraction_method.value,
        "algorithm_version": item.algorithm_version,
    }


def _candidate_from_dict(value: dict[str, Any]) -> TermCandidate:
    scope = value["scope"]
    return TermCandidate(
        str(value["candidate_id"]),
        str(value["original"]),
        str(value["translation"]),
        str(value["normalized_original"]),
        str(value["normalized_translation"]),
        tuple(str(item) for item in value["evidence_ids"]),
        TermScope(scope["kind"], scope.get("plugin_id")),
        ExtractionMethod(str(value["extraction_method"])),
        str(value["algorithm_version"]),
    )


def _json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()


def _read_payload(payload: bytes, schema: str) -> dict[str, Any]:
    value = json.loads(payload)
    if not isinstance(value, dict) or value.get("schema") != schema:
        raise ValueError("cache payload schema mismatch")
    return value


__all__ = [
    "BUILD_CACHE_SCHEMA",
    "BuildReuseDecision",
    "EXTRACTION_KEY_SCHEMA",
    "IncrementalBuildOutcome",
    "IncrementalBuildPlanner",
    "IncrementalCachePort",
    "IncrementalTerminologyBuilder",
    "PARSE_KEY_SCHEMA",
    "RecomputePlan",
    "RelationComponentDigest",
    "parse_content_key",
]
