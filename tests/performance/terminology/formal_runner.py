"""Formal FR5.16 benchmark workloads built from production terminology services."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
import gc
import json
from pathlib import Path
import threading
import time
from typing import Any

from tests.performance.measure import current_rss_bytes
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import (
    EetXmlAdapter,
    FormatId,
    LocalizedStringsAdapter,
    ParatranzJsonAdapter,
    ParseRequest,
    SourceDescriptor,
    SsePluginAdapter,
    XtXmlAdapter,
)
from transbridge.application.terminology.diff import CanonicalDiffEngine
from transbridge.application.terminology.extraction import DeterministicTermExtractor, TerminologyExtractionService
from transbridge.application.terminology.identity import canonical_digest, term_id
from transbridge.application.terminology.models import (
    BilingualEvidence,
    BuildResult,
    BuildResultRef,
    BuildSummary,
    DecisionStatus,
    TermCandidate,
    TermDecision,
    TerminologyVersion,
    TerminologyVersionRef,
)
from transbridge.application.terminology.narrative import ChangeNarrativeProjector
from transbridge.application.terminology.ports import PageRequest
from transbridge.application.terminology.reducer import CanonicalTerminologyReducer
from transbridge.application.terminology.renderers.changelog_excel import ChangeLogExcelRenderer
from transbridge.application.terminology.renderers.changelog_markdown import ChangeLogMarkdownRenderer
from transbridge.application.terminology.renderers.quality_excel import QualityExcelRenderer
from transbridge.application.terminology.report_queries import TerminologyReportQueryService
from transbridge.application.terminology.reports import NoDraftIdentity, TerminologyReportSnapshotFactory
from transbridge.persistence.terminology import SqliteTerminologyRepository

from .dataset import GeneratedTerminologyDataset
from .measure import BenchmarkRun, PhaseTiming, TerminologyPhase, measure_phase

PROJECT_ID = "benchmark-project"
VARIANT_ID = "main"


class FormalBenchmarkExecutor:
    """Prepare scenario state outside samples and retain five raw production samples."""

    def __init__(self, dataset: GeneratedTerminologyDataset, root: Path) -> None:
        self.dataset = dataset
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.repository = SqliteTerminologyRepository.open(self.root / "repository", PROJECT_ID)
        self._rows: tuple[dict[str, Any], ...] | None = None
        self._build: BuildResult | None = None
        self._decisions: tuple[TermDecision, ...] | None = None
        self._history: tuple[TerminologyVersion, TerminologyVersion] | None = None
        self._incremental_baseline: dict[str, tuple[TermCandidate, ...]] | None = None

    def close(self) -> None:
        self.repository.close()

    def prepare(self, scenario: str) -> dict[str, Any]:
        preparation = formal_cache_preparation(scenario)
        source_index = json.loads(self.dataset.source_index_file.read_text(encoding="utf-8"))
        _validate_source_manifest(self.dataset, source_index)
        preparation["production_adapter_preflight"] = {
            "formats": [item["format_id"] for item in source_index["templates"]],
            "result": "all-five-fixtures-parsed",
            "bulk_logical_evidence": "deterministic-ndjson-contract-corpus",
        }
        if scenario == "full-cold":
            return preparation
        if scenario == "full-warm":
            self._load_rows()
            self._complete_build(persist=False)
            preparation["warmup_completed"] = True
            return preparation
        if scenario in {"repeat", "query", "history", "compare", "report", "changelog"}:
            self._ensure_build()
        if scenario == "repeat":
            preparation["prepared_build_ref"] = self._build.ref.build_key
        elif scenario == "changed-10pct":
            self._prepare_incremental_baseline()
            preparation["baseline_component_count"] = len(self._incremental_baseline or {})
        elif scenario in {"history", "compare"}:
            self._ensure_history()
            preparation["prepared_version_ids"] = [item.ref.version_id for item in self._history or ()]
        return preparation

    def run(self, scenario: str, iteration: int) -> BenchmarkRun:
        gc.collect()
        sampler = _RssSampler()
        sampler.start()
        timings: list[PhaseTiming] = []
        try:
            if scenario in {"full-cold", "full-warm"}:
                self._run_full(timings, cold=scenario == "full-cold", iteration=iteration)
            elif scenario == "repeat":
                self._run_repeat(timings)
            elif scenario == "changed-10pct":
                self._run_changed(timings)
            elif scenario == "query":
                self._run_query(timings)
            elif scenario == "history":
                self._run_history(timings)
            elif scenario == "compare":
                self._run_compare(timings)
            elif scenario == "report":
                self._run_report(timings, iteration)
            elif scenario == "changelog":
                self._run_changelog(timings, iteration)
            elif scenario == "cancel":
                self._run_cancel(timings)
            else:  # pragma: no cover - guarded by CLI and manifest contracts
                raise ValueError(f"unknown formal benchmark scenario: {scenario}")
            self._append_external_wait_buckets(timings)
        finally:
            sampler.stop()
        with measure_phase(TerminologyPhase.CLEANUP, timings):
            gc.collect()
        recovered = _rss()
        peak = max(sampler.peak, recovered)
        return BenchmarkRun(
            iteration,
            tuple(timings),
            max(0, peak - sampler.baseline),
            "psutil-rss-baseline/10ms-sampled-peak/post-gc-recovered",
            sampler.baseline,
            peak,
            recovered,
        )

    def _run_full(self, timings: list[PhaseTiming], *, cold: bool, iteration: int) -> None:
        if cold:
            rows = None
            repository = SqliteTerminologyRepository.open(self.root / f"cold-{iteration}", PROJECT_ID)
        else:
            rows = self._rows
            repository = self.repository
        try:
            with measure_phase(TerminologyPhase.CAPTURE, timings):
                source_index = json.loads(self.dataset.source_index_file.read_text(encoding="utf-8"))
            with measure_phase(TerminologyPhase.PARSE, timings):
                _validate_source_manifest(self.dataset, source_index)
                if rows is None:
                    rows = _read_rows(self.dataset)
            result = self._complete_build(timings=timings, rows=rows, persist=False)
            with measure_phase(TerminologyPhase.PERSIST, timings):
                repository.put_build(result)
        finally:
            if cold:
                repository.close()

    def _run_repeat(self, timings: list[PhaseTiming]) -> None:
        build = self._require_build()
        with measure_phase(TerminologyPhase.CAPTURE, timings, details=(("input_digest", self.dataset.spec.seed),)):
            current_manifest = json.loads(self.dataset.source_index_file.read_text(encoding="utf-8"))
            _validate_source_manifest(self.dataset, current_manifest)
        with measure_phase(TerminologyPhase.QUERY, timings, details=(("reuse", "exact-build-ref"),)):
            reused = self.repository.get_build(build.ref)
            if reused.ref.content_digest != build.ref.content_digest:
                raise RuntimeError("unchanged build reuse returned a different canonical digest")

    def _run_changed(self, timings: list[PhaseTiming]) -> None:
        rows = self._load_rows()
        baseline = self._incremental_baseline
        if baseline is None:
            raise RuntimeError("incremental baseline was not prepared")
        evidence_components = _evidence_components(rows, maximum_component_size=max(1, len(rows) // 10))
        changed_components = _changed_components(evidence_components, maximum=max(1, len(rows) // 10))
        changed_ids = {
            item.evidence_id for component_id in changed_components for item in evidence_components[component_id]
        }
        with measure_phase(TerminologyPhase.CAPTURE, timings):
            changed_rows = tuple(
                {**row, "translation": f"{row['translation']} changed"} if row["evidence_id"] in changed_ids else row
                for row in rows
            )
        with measure_phase(
            TerminologyPhase.PARSE,
            timings,
            details=(
                ("reused_components", len(baseline) - len(changed_components)),
                ("reparsed_components", len(changed_components)),
            ),
        ):
            changed_evidence_components = _evidence_components(
                changed_rows,
                maximum_component_size=max(1, len(rows) // 10),
            )
        with measure_phase(TerminologyPhase.ASSEMBLE, timings):
            changed_evidence = len(changed_ids)
            if changed_evidence > len(rows) // 10:
                raise RuntimeError("changed evidence exceeded the formal 10 percent boundary")
        with measure_phase(TerminologyPhase.EXTRACT, timings):
            extractor = DeterministicTermExtractor()
            candidates_by_component = dict(baseline)
            for component_id in changed_components:
                candidates_by_component[component_id] = extractor.extract(changed_evidence_components[component_id])
        with measure_phase(
            TerminologyPhase.REDUCE,
            timings,
            details=(
                ("changed_evidence", changed_evidence),
                ("total_evidence", len(rows)),
                ("reused_components", len(baseline) - len(changed_components)),
                ("recomputed_components", len(changed_components)),
            ),
        ):
            reducer = CanonicalTerminologyReducer()
            incremental = reducer.reduce(
                project_id=PROJECT_ID,
                variant_id=VARIANT_ID,
                candidates=tuple(
                    item
                    for component_id in sorted(candidates_by_component)
                    for item in candidates_by_component[component_id]
                ),
            )
            full = reducer.reduce(
                project_id=PROJECT_ID,
                variant_id=VARIANT_ID,
                candidates=extractor.extract(tuple(_evidence(row) for row in changed_rows)),
            )
            incremental_digest = _reduction_digest(incremental)
            full_digest = _reduction_digest(full)
            if incremental_digest != full_digest:
                raise RuntimeError("incremental/full canonical digest parity failed")
        with measure_phase(TerminologyPhase.PERSIST, timings):
            self.repository.put_build(_build_result(tuple(_evidence(row) for row in changed_rows), incremental))

    def _run_query(self, timings: list[PhaseTiming]) -> None:
        build = self._require_build()
        with measure_phase(TerminologyPhase.QUERY, timings):
            candidates = self.repository.list_candidates(
                build.ref,
                PageRequest(limit=1000, query_fingerprint="stable-id:first-page"),
            )
            conflicts = self.repository.list_conflicts(
                build.ref,
                PageRequest(limit=1000, query_fingerprint="stable-id:first-page"),
            )
            if candidates.total != build.summary.candidate_count or conflicts.total != build.summary.conflict_count:
                raise RuntimeError("paged query totals do not match the persisted build")

    def _run_compare(self, timings: list[PhaseTiming]) -> None:
        history = self._history
        if history is None:
            raise RuntimeError("version history was not prepared")
        with measure_phase(TerminologyPhase.HISTORY, timings):
            page = self.repository.list_versions(PROJECT_ID, VARIANT_ID, PageRequest(limit=1000))
            if len(page.items) < 2:
                raise RuntimeError("compare scenario requires two persisted versions")
        with measure_phase(TerminologyPhase.COMPARE, timings):
            parent = self.repository.get_version(history[0].ref)
            target = self.repository.get_version(history[1].ref)
            diff = CanonicalDiffEngine().compare(
                parent,
                target_version_id=target.ref.version_id,
                decisions=target.decisions,
                conflicts=target.conflicts,
                manual_actions=target.manual_actions,
            )
            if diff.content_digest != target.canonical_diff.content_digest:
                raise RuntimeError("persisted version comparison is not reproducible")

    def _run_history(self, timings: list[PhaseTiming]) -> None:
        history = self._history
        if history is None:
            raise RuntimeError("version history was not prepared")
        with measure_phase(TerminologyPhase.HISTORY, timings):
            page = self.repository.list_versions(
                PROJECT_ID,
                VARIANT_ID,
                PageRequest(limit=1, query_fingerprint="version-id:first-page"),
            )
            if page.total != len(history) or len(page.items) != 1 or page.next_cursor is None:
                raise RuntimeError("persisted version history pagination did not retain its full snapshot")

    def _run_report(self, timings: list[PhaseTiming], iteration: int) -> None:
        build = self._require_build()
        decisions = self._require_decisions()
        started = time.perf_counter()
        details: tuple[tuple[str, Any], ...] = ()
        try:
            snapshot = TerminologyReportSnapshotFactory(self.repository).freeze(
                build.ref,
                no_draft=NoDraftIdentity(PROJECT_ID, VARIANT_ID, None, build.ref.content_digest),
                terms=decisions,
            )
            self.repository.put_report_snapshot(snapshot)
            artifact = QualityExcelRenderer(TerminologyReportQueryService(self.repository)).render(
                snapshot.ref,
                self.root / "artifacts" / f"quality-{iteration}.xlsx",
            )
            if artifact.semantic_manifest.change_count != len(decisions) + len(build.conflicts):
                raise RuntimeError("quality renderer truncated frozen report rows")
            details = (
                ("renderer", artifact.renderer_version),
                ("sha256", artifact.sha256),
                ("size_bytes", artifact.size),
                ("semantic_rows", artifact.semantic_manifest.change_count),
                ("sheet_count", len(artifact.sheet_names)),
            )
        finally:
            timings.append(PhaseTiming(TerminologyPhase.REPORT, time.perf_counter() - started, details))

    def _run_changelog(self, timings: list[PhaseTiming], iteration: int) -> None:
        decisions = self._require_decisions()
        build = self._require_build()
        repository = SqliteTerminologyRepository.open(self.root / f"changelog-{iteration}", PROJECT_ID)
        try:
            started = time.perf_counter()
            details: tuple[tuple[str, Any], ...] = ()
            try:
                repository.put_build(build)
                version_id = f"changelog-{iteration}"
                diff = CanonicalDiffEngine().compare(None, target_version_id=version_id, decisions=decisions)
                version = _version(build, version_id, None, decisions, diff)
                repository.publish_version(version, expected_effective_version_id=None)
                document = ChangeNarrativeProjector().project(
                    version_ref=version.ref,
                    diff=diff,
                    decisions=decisions,
                    conflicts=build.conflicts,
                    manual_actions=(),
                )
                repository.put_changelog(document)
                markdown = ChangeLogMarkdownRenderer(repository.changelogs).render(
                    document.ref,
                    self.root / "artifacts" / f"changelog-{iteration}.md",
                )
                excel = ChangeLogExcelRenderer(repository.changelogs).render(
                    document.ref,
                    self.root / "artifacts" / f"changelog-{iteration}.xlsx",
                )
                if markdown.semantic_manifest != excel.semantic_manifest:
                    raise RuntimeError("Markdown/Excel changelog semantic parity failed")
                if markdown.semantic_manifest.change_count != len(decisions):
                    raise RuntimeError("changelog renderer truncated canonical changes")
                details = (
                    ("markdown_renderer", markdown.renderer_version),
                    ("markdown_sha256", markdown.sha256),
                    ("markdown_size_bytes", markdown.size),
                    ("excel_renderer", excel.renderer_version),
                    ("excel_sha256", excel.sha256),
                    ("excel_size_bytes", excel.size),
                    ("semantic_changes", markdown.semantic_manifest.change_count),
                    ("semantic_manifest_parity", True),
                    ("excel_sheet_count", len(excel.sheet_names)),
                )
            finally:
                timings.append(PhaseTiming(TerminologyPhase.CHANGELOG, time.perf_counter() - started, details))
        finally:
            repository.close()

    @staticmethod
    def _run_cancel(timings: list[PhaseTiming]) -> None:
        evidence = BilingualEvidence(
            "cancel-evidence",
            PROJECT_ID,
            VARIANT_ID,
            ("cancel-source",),
            "project-source:cancel-source",
            "cancel/1",
            "Dragon guards",
            "龙守卫",
            "json-transbridge",
            "cancel-fingerprint",
            "BOOK:DESC",
            "translated",
        )
        started = time.perf_counter()
        with measure_phase(TerminologyPhase.CANCEL, timings):
            result = TerminologyExtractionService(llm=_UnexpectedLlm()).extract(
                (evidence,),
                llm_enabled=True,
                cancellation=_Cancelled(),
            )
            if not result.cancelled:
                raise RuntimeError("production extraction cancellation boundary was not observed")
        if time.perf_counter() - started > 3.0:
            raise RuntimeError("cancellation terminal state exceeded three seconds")

    def _complete_build(
        self,
        *,
        timings: list[PhaseTiming] | None = None,
        rows: tuple[dict[str, Any], ...] | None = None,
        persist: bool = True,
    ) -> BuildResult:
        rows = rows or self._load_rows()
        target = timings if timings is not None else []
        with measure_phase(TerminologyPhase.ASSEMBLE, target):
            evidence = tuple(_evidence(row) for row in rows)
        with measure_phase(TerminologyPhase.EXTRACT, target):
            candidates = DeterministicTermExtractor().extract(evidence)
        with measure_phase(TerminologyPhase.REDUCE, target):
            reduced = CanonicalTerminologyReducer().reduce(
                project_id=PROJECT_ID,
                variant_id=VARIANT_ID,
                candidates=candidates,
            )
        result = _build_result(evidence, reduced)
        if persist:
            self.repository.put_build(result)
        return result

    def _ensure_build(self) -> None:
        if self._build is None:
            self._build = self._complete_build()
            self._decisions = _decisions(self._build.candidates)

    def _require_build(self) -> BuildResult:
        self._ensure_build()
        if self._build is None:  # pragma: no cover - narrowed by _ensure_build
            raise RuntimeError("benchmark build is unavailable")
        return self._build

    def _require_decisions(self) -> tuple[TermDecision, ...]:
        self._ensure_build()
        if self._decisions is None:  # pragma: no cover - narrowed by _ensure_build
            raise RuntimeError("benchmark decisions are unavailable")
        return self._decisions

    def _load_rows(self) -> tuple[dict[str, Any], ...]:
        if self._rows is None:
            self._rows = _read_rows(self.dataset)
        return self._rows

    def _prepare_incremental_baseline(self) -> None:
        if self._incremental_baseline is not None:
            return
        extractor = DeterministicTermExtractor()
        self._incremental_baseline = {
            component_id: extractor.extract(evidence)
            for component_id, evidence in _evidence_components(
                self._load_rows(),
                maximum_component_size=max(1, len(self._load_rows()) // 10),
            ).items()
        }

    def _ensure_history(self) -> None:
        if self._history is not None:
            return
        build = self._require_build()
        decisions = self._require_decisions()
        first_diff = CanonicalDiffEngine().compare(None, target_version_id="history-v1", decisions=decisions)
        first = _version(build, "history-v1", None, decisions, first_diff)
        self.repository.publish_version(first, expected_effective_version_id=None)
        changed = (replace(decisions[0], translation=f"{decisions[0].translation} changed"), *decisions[1:])
        second_diff = CanonicalDiffEngine().compare(
            first,
            target_version_id="history-v2",
            decisions=changed,
        )
        second = _version(build, "history-v2", first.ref.version_id, tuple(changed), second_diff)
        self.repository.publish_version(second, expected_effective_version_id=first.ref.version_id)
        self._history = (first, second)

    @staticmethod
    def _append_external_wait_buckets(timings: list[PhaseTiming]) -> None:
        timings.extend((
            PhaseTiming(
                TerminologyPhase.EXTERNAL_LLM_WAIT,
                0.0,
                (("status", "disabled"), ("requests", 0), ("retries", 0)),
            ),
            PhaseTiming(
                TerminologyPhase.EXTERNAL_IO_WAIT,
                0.0,
                (("status", "none"), ("local_disk_io", "included-in-local-phases")),
            ),
        ))


def formal_cache_preparation(scenario: str) -> dict[str, Any]:
    common: dict[str, Any] = {
        "dataset_generation": "completed-before-five-formal-samples",
        "os_file_cache": "operator-recorded-not-programmatically-cleared",
        "external_network_cache": "not-used",
        "llm_cache": "disabled",
    }
    policies = {
        "full-cold": {
            "repository": "new-empty-sqlite-directory-per-sample",
            "python_objects": "gc-before-each-sample; rows-read-from-disk-per-sample",
        },
        "full-warm": {
            "repository": "shared-sqlite-repository",
            "python_objects": "rows-and-reduction-warmed-once-before-five-samples",
        },
        "repeat": {"repository": "complete-build-persisted-before-five-samples", "reuse": "exact-build-ref-query"},
        "changed-10pct": {
            "repository": "shared-sqlite-repository",
            "baseline": "per-source-extraction-components-prepared-before-five-samples",
            "change_policy": "whole-source-components-selected-with-evidence-count-at-most-floor(total*0.10)",
            "parity": "each-incremental-sample-compared-with-clean-full-reduction-of-changed-input",
        },
        "query": {"repository": "complete-build-persisted-before-five-samples"},
        "history": {"repository": "immutable-version-history-persisted-before-five-samples"},
        "compare": {"repository": "two-immutable-versions-persisted-before-five-samples"},
        "report": {"repository": "complete-build-persisted-before-five-samples"},
        "changelog": {"repository": "complete-build-persisted-before-five-samples"},
        "cancel": {"repository": "not-required", "cancellation": "pre-cancelled-before-llm-scheduling"},
    }
    return {**common, **policies[scenario]}


def _read_rows(dataset: GeneratedTerminologyDataset) -> tuple[dict[str, Any], ...]:
    with dataset.evidence_file.open(encoding="utf-8") as stream:
        return tuple(json.loads(line) for line in stream)


def _validate_source_manifest(dataset: GeneratedTerminologyDataset, source_index: dict[str, Any]) -> None:
    if len(source_index.get("templates", ())) != 5:
        raise RuntimeError("formal corpus must retain all five production adapter templates")
    adapters = {
        FormatId.PLUGIN_SSE: SsePluginAdapter(),
        FormatId.XML_EET: EetXmlAdapter(),
        FormatId.XML_XT: XtXmlAdapter(),
        FormatId.STRINGS: LocalizedStringsAdapter(FormatId.STRINGS),
        FormatId.JSON_PARATRANZ: ParatranzJsonAdapter(),
    }
    for template in source_index["templates"]:
        path = dataset.root / template["path"]
        if not path.is_file() or path.stat().st_size != template["size_bytes"]:
            raise RuntimeError(f"formal adapter source is missing or changed: {path}")
        format_id = FormatId(template["format_id"])
        parsed = adapters[format_id].parse(
            ParseRequest(
                SourceDescriptor(str(path), path.name, path.stat().st_size),
                RequestContext("terminology-formal-benchmark", run_id=f"adapter-{format_id.value}"),
                format_id,
            )
        )
        if parsed.outcome not in {OperationOutcome.COMPLETED, OperationOutcome.PARTIAL}:
            raise RuntimeError(f"production adapter rejected formal fixture: {format_id.value}")


def _evidence(row: dict[str, Any]) -> BilingualEvidence:
    return BilingualEvidence(
        row["evidence_id"],
        PROJECT_ID,
        VARIANT_ID,
        (row["source_id"],),
        f"project-source:{row['source_id']}",
        row["locator"],
        row["original"],
        row["translation"],
        row["format_id"],
        "benchmark-source-fingerprint",
        "NPC_:FULL",
        "translated",
    )


def _evidence_components(
    rows: tuple[dict[str, Any], ...],
    *,
    maximum_component_size: int,
) -> dict[str, tuple[BilingualEvidence, ...]]:
    values: defaultdict[str, list[BilingualEvidence]] = defaultdict(list)
    for row in rows:
        values[row["source_id"]].append(_evidence(row))
    components: dict[str, tuple[BilingualEvidence, ...]] = {}
    for source_id, items in sorted(values.items()):
        for index in range(0, len(items), maximum_component_size):
            components[f"{source_id}:{index // maximum_component_size:06d}"] = tuple(
                items[index : index + maximum_component_size]
            )
    return components


def _changed_components(
    components: dict[str, tuple[BilingualEvidence, ...]],
    *,
    maximum: int,
) -> frozenset[str]:
    selected: list[str] = []
    selected_count = 0
    for component_id, evidence in sorted(components.items(), key=lambda item: (len(item[1]), item[0])):
        count = len(evidence)
        if selected and selected_count + count > maximum:
            break
        if count <= maximum:
            selected.append(component_id)
            selected_count += count
    if not selected:
        raise RuntimeError("dataset has no reusable component within the 10 percent incremental boundary")
    return frozenset(selected)


def _reduction_digest(reduced: Any) -> str:
    return canonical_digest(
        {"candidates": reduced.candidates, "conflicts": reduced.conflicts},
        namespace="terminology.benchmark-reduction.v2",
    )


def _build_result(evidence: tuple[BilingualEvidence, ...], reduced: Any) -> BuildResult:
    digest = _reduction_digest(reduced)
    return BuildResult(
        BuildResultRef(f"benchmark-build:{digest.rsplit(':', 1)[-1]}", digest),
        PROJECT_ID,
        VARIANT_ID,
        BuildSummary(
            len({item.source_chain[0] for item in evidence}),
            len(evidence),
            len(reduced.candidates),
            len(reduced.conflicts),
        ),
        evidence,
        reduced.candidates,
        reduced.conflicts,
    )


def _decisions(candidates: tuple[TermCandidate, ...]) -> tuple[TermDecision, ...]:
    chosen: dict[str, TermCandidate] = {}
    for candidate in sorted(candidates, key=lambda item: (item.normalized_original, item.candidate_id)):
        chosen.setdefault(candidate.normalized_original, candidate)
    return tuple(
        sorted(
            (
                TermDecision(
                    term_id=term_id(
                        project_id=PROJECT_ID,
                        variant_id=VARIANT_ID,
                        scope=candidate.scope,
                        original=candidate.original,
                    ),
                    project_id=PROJECT_ID,
                    variant_id=VARIANT_ID,
                    original=candidate.original,
                    normalized_original=candidate.normalized_original,
                    translation=candidate.translation,
                    scope=candidate.scope,
                    status=DecisionStatus.ADOPTED,
                    evidence_ids=candidate.evidence_ids,
                )
                for candidate in chosen.values()
            ),
            key=lambda item: item.term_id,
        )
    )


def _version(build: BuildResult, version_id: str, parent_id: str | None, decisions, diff) -> TerminologyVersion:
    ref = TerminologyVersionRef(
        version_id,
        PROJECT_ID,
        VARIANT_ID,
        canonical_digest(
            {"version_id": version_id, "parent": parent_id, "decisions": decisions, "diff": diff},
            namespace="terminology.benchmark-version.v1",
        ),
    )
    return TerminologyVersion(
        ref,
        parent_id,
        build.ref,
        1,
        1,
        build.completeness,
        "2026-01-01T00:00:00Z",
        decisions,
        diff,
    )


class _Cancelled:
    is_cancelled = True


class _UnexpectedLlm:
    def extract(self, batch: tuple[Any, ...]) -> tuple[Any, ...]:
        raise AssertionError("cancelled workload must not schedule an LLM batch")


class _RssSampler:
    def __init__(self) -> None:
        self.baseline = _rss()
        self.peak = self.baseline
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, name="terminology-rss-sampler", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)
        self.peak = max(self.peak, _rss())

    def _sample(self) -> None:
        while not self._stop.wait(0.01):
            self.peak = max(self.peak, _rss())


def _rss() -> int:
    return int(current_rss_bytes())


__all__ = ["FormalBenchmarkExecutor", "formal_cache_preparation"]
