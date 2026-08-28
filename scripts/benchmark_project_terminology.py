"""Generate reproducible FR5.16 diagnostic or formal benchmark evidence.

Diagnostic smoke is intentionally gate-ineligible.  Formal mode executes one
scenario's production semantics and can become eligible only when the manifest
also proves the fixed regular/stress corpus and calibrated reference host.
"""

from __future__ import annotations

import argparse
import gc
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.performance.measure import current_rss_bytes  # noqa: E402
from tests.performance.terminology.dataset import (  # noqa: E402
    DATASET_SPECS,
    dataset_manifest,
    generate_terminology_dataset,
)
from tests.performance.terminology.formal_runner import FormalBenchmarkExecutor  # noqa: E402
from tests.performance.terminology.measure import (  # noqa: E402
    FORMAL_REPETITIONS,
    SCENARIOS,
    BenchmarkEvidenceProfile,
    BenchmarkManifest,
    BenchmarkRun,
    PhaseTiming,
    TerminologyPhase,
    measure_phase,
)
from transbridge import __version__  # noqa: E402
from transbridge.application.contracts import OperationOutcome, RequestContext  # noqa: E402
from transbridge.application.io import (  # noqa: E402
    EetXmlAdapter,
    FormatId,
    LocalizedStringsAdapter,
    ParatranzJsonAdapter,
    ParseRequest,
    SourceDescriptor,
    SsePluginAdapter,
    XtXmlAdapter,
)
from transbridge.application.terminology.diff import CanonicalDiffEngine  # noqa: E402
from transbridge.application.terminology.extraction import (  # noqa: E402
    DeterministicTermExtractor,
    TerminologyExtractionService,
)
from transbridge.application.terminology.identity import canonical_digest, term_id  # noqa: E402
from transbridge.application.terminology.models import (  # noqa: E402
    BilingualEvidence,
    BuildResult,
    BuildResultRef,
    BuildSummary,
    DecisionStatus,
    TermDecision,
    TerminologyVersionRef,
)
from transbridge.application.terminology.narrative import ChangeNarrativeProjector  # noqa: E402
from transbridge.application.terminology.ports import PageRequest  # noqa: E402
from transbridge.application.terminology.reducer import CanonicalTerminologyReducer  # noqa: E402
from transbridge.persistence.terminology import SqliteTerminologyRepository  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = _argument_parser()
    args = parser.parse_args(argv)
    try:
        environment = collect_environment(
            cpu_model=args.cpu_model,
            physical_cores=args.physical_cores,
            memory_bytes=args.memory_bytes,
            disk_type=args.disk_type,
            reference_device_id=args.reference_device_id,
        )
    except ValueError as exc:
        parser.error(str(exc))

    output = args.output or _default_output(args.profile, args.scale, args.scenario)
    if output.exists() and not args.overwrite:
        parser.error(f"benchmark evidence already exists: {output}; pass --overwrite to replace it")

    with tempfile.TemporaryDirectory(prefix="transbridge-terminology-") as temporary:
        dataset = generate_terminology_dataset(Path(temporary) / args.scale, DATASET_SPECS[args.scale])
        if args.profile == "formal":
            executor = FormalBenchmarkExecutor(dataset, Path(temporary) / "formal-workload")
            try:
                cache_preparation = executor.prepare(args.scenario)
                runs = tuple(executor.run(args.scenario, iteration) for iteration in range(1, FORMAL_REPETITIONS + 1))
            finally:
                executor.close()
            manifest = _formal_manifest(
                args.scenario,
                environment,
                dataset_manifest(dataset),
                cache_preparation,
                runs,
            )
        else:
            cache_preparation = _cache_preparation(args.scenario)
            runs = _run_production_workload(dataset, args.scenario)
            manifest = _diagnostic_manifest(
                args.scenario,
                environment,
                dataset_manifest(dataset),
                cache_preparation,
                runs,
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(manifest.to_json(), encoding="utf-8", newline="\n")

    print(f"wrote {output}")
    print(f"artifact digest: {manifest.artifact_digest}")
    evidence = manifest.to_dict()["evidence"]
    print(f"single-scenario evidence contract eligible: {str(evidence['gate_eligible']).lower()}")
    print("release gate eligible: false (requires aggregated regular+stress bundle and budget evaluation)")
    for reason in evidence["gate_ineligibility_reasons"]:
        print(f"gate ineligibility: {reason}")
    if not environment["reference_requirements_met"]:
        print("warning: this host is recorded but is not an FR5.16 calibrated Windows 11 reference device")
    return 0


def _formal_manifest(
    scenario: str,
    environment: dict[str, Any],
    dataset: dict[str, Any],
    cache_preparation: dict[str, Any],
    runs: tuple[BenchmarkRun, ...],
) -> BenchmarkManifest:
    from tests.performance.terminology.measure import (
        FORMAL_WORKLOAD,
        SCENARIO_REQUIRED_SEMANTICS,
    )

    return BenchmarkManifest(
        scenario=scenario,
        environment=environment,
        dataset=dataset,
        cache_preparation=cache_preparation,
        runs=runs,
        workload=FORMAL_WORKLOAD,
        evidence_profile=BenchmarkEvidenceProfile.FORMAL_GATE,
        completed_semantics=SCENARIO_REQUIRED_SEMANTICS[scenario],
    )


def _diagnostic_manifest(
    scenario: str,
    environment: dict[str, Any],
    dataset: dict[str, Any],
    cache_preparation: dict[str, Any],
    runs: tuple[BenchmarkRun, ...],
) -> BenchmarkManifest:
    return BenchmarkManifest(
        scenario=scenario,
        environment=environment,
        dataset=dataset,
        cache_preparation=cache_preparation,
        runs=runs,
        workload="project-terminology-diagnostic-smoke-v2",
        evidence_profile=BenchmarkEvidenceProfile.DIAGNOSTIC_SMOKE,
        known_limitations=(
            "whole-smoke-pipeline-repeated-instead-of-formal-per-scenario-workload",
            "fresh-repository-created-for-every-iteration",
        ),
    )


def collect_environment(
    *,
    cpu_model: str | None,
    physical_cores: int | None,
    memory_bytes: int | None,
    disk_type: str,
    reference_device_id: str,
) -> dict[str, Any]:
    """Collect required reference fields, rejecting silent unknown values."""

    resolved_cpu = cpu_model or platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER")
    resolved_physical = physical_cores or _psutil_value("cpu_count", logical=False)
    resolved_logical = _psutil_value("cpu_count", logical=True) or os.cpu_count()
    resolved_memory = memory_bytes or _total_memory_bytes()
    missing = [
        label
        for label, value in (
            ("CPU model (--cpu-model)", resolved_cpu),
            ("physical core count (--physical-cores)", resolved_physical),
            ("memory bytes (--memory-bytes)", resolved_memory),
        )
        if not value
    ]
    if missing:
        raise ValueError("required reference fields could not be detected; provide " + ", ".join(missing))

    windows_release = platform.release()
    is_windows_11 = platform.system() == "Windows" and _windows_build_number() >= 22_000
    requirements_met = (
        is_windows_11
        and int(resolved_physical) >= 4
        and int(resolved_memory) >= 16 * 1024**3
        and disk_type.lower() in {"ssd", "nvme", "sata-ssd"}
    )
    return {
        "reference_device_id": reference_device_id,
        "cpu_model": str(resolved_cpu),
        "physical_cores": int(resolved_physical),
        "logical_cores": int(resolved_logical) if resolved_logical else None,
        "windows": {
            "release": windows_release,
            "version": platform.version(),
            "platform": platform.platform(),
            "build": _windows_build_number(),
        },
        "memory_bytes": int(resolved_memory),
        "disk_type": disk_type,
        "python": platform.python_version(),
        "transbridge_build": __version__,
        "measurement_tools": {
            "clock": "time.perf_counter",
            "psutil": _package_version("psutil"),
            "pytest": _package_version("pytest"),
        },
        "reference_requirements_met": requirements_met,
    }


def _run_production_workload(dataset: Any, scenario: str) -> tuple[BenchmarkRun, ...]:
    runs: list[BenchmarkRun] = []
    for iteration in range(1, FORMAL_REPETITIONS + 1):
        if scenario == "full-cold":
            gc.collect()
        timings: list[PhaseTiming] = []
        with measure_phase(TerminologyPhase.CAPTURE, timings):
            source_index = json.loads(dataset.source_index_file.read_text(encoding="utf-8"))
            history_payload = json.loads(dataset.version_history_file.read_text(encoding="utf-8"))
        with measure_phase(TerminologyPhase.PARSE, timings):
            _validate_real_adapters(dataset, source_index)
            rows = [json.loads(line) for line in dataset.evidence_file.read_text(encoding="utf-8").splitlines()]
            if scenario == "changed-10pct":
                changed_count = len(rows) // 10
                rows = [
                    {**row, "translation": f"{row['translation']} changed"} if index < changed_count else row
                    for index, row in enumerate(rows)
                ]
            else:
                changed_count = 0
        with measure_phase(
            TerminologyPhase.ASSEMBLE,
            timings,
            details=(("changed_evidence", changed_count), ("total_evidence", len(rows))),
        ):
            evidence = tuple(_evidence(row) for row in rows)
        with measure_phase(TerminologyPhase.EXTRACT, timings, details=(("llm", "disabled"),)):
            candidates = DeterministicTermExtractor().extract(evidence)
        with measure_phase(TerminologyPhase.REDUCE, timings):
            reducer = CanonicalTerminologyReducer()
            reduced = reducer.reduce(project_id="benchmark-project", variant_id="main", candidates=candidates)
            parity = reducer.reduce(
                project_id="benchmark-project",
                variant_id="main",
                candidates=tuple(reversed(candidates)),
            )
            result_digest = canonical_digest(reduced, namespace="terminology.benchmark-reduction.v1")
            parity_digest = canonical_digest(parity, namespace="terminology.benchmark-reduction.v1")
            if result_digest != parity_digest:
                raise RuntimeError("canonical digest parity failed for reversed full input")
        with tempfile.TemporaryDirectory(prefix="terminology-production-run-") as run_temp:
            repository = SqliteTerminologyRepository.open(run_temp, "benchmark-project")
            result = BuildResult(
                BuildResultRef(f"benchmark-build-{iteration}", result_digest),
                "benchmark-project",
                "main",
                BuildSummary(
                    dataset.spec.source_count,
                    len(evidence),
                    len(reduced.candidates),
                    len(reduced.conflicts),
                ),
                evidence,
                reduced.candidates,
                reduced.conflicts,
            )
            with measure_phase(TerminologyPhase.PERSIST, timings):
                repository.put_build(result)
            with measure_phase(TerminologyPhase.QUERY, timings):
                candidate_page = repository.list_candidates(result.ref, PageRequest(limit=100))
                conflict_page = repository.list_conflicts(result.ref, PageRequest(limit=100))
            with measure_phase(TerminologyPhase.HISTORY, timings):
                history_count = len(history_payload["versions"])
            decisions = _decisions(reduced.candidates, reduced.conflicts)
            with measure_phase(TerminologyPhase.COMPARE, timings):
                diff = CanonicalDiffEngine().compare(None, target_version_id="benchmark-v1", decisions=decisions)
            with measure_phase(TerminologyPhase.REPORT, timings):
                json.dumps(
                    {
                        "summary": result.summary.candidate_count,
                        "first_terms": len(candidate_page.items),
                        "first_conflicts": len(conflict_page.items),
                        "history": history_count,
                    },
                    ensure_ascii=False,
                )
            with measure_phase(TerminologyPhase.CHANGELOG, timings):
                document = ChangeNarrativeProjector().project(
                    version_ref=TerminologyVersionRef("benchmark-v1", "benchmark-project", "main", result_digest),
                    diff=diff,
                    decisions=decisions,
                    conflicts=reduced.conflicts,
                    manual_actions=(),
                )
                if len(document.changes) != len(diff.changes):
                    raise RuntimeError("changelog projection dropped canonical changes")
            with measure_phase(TerminologyPhase.CANCEL, timings):
                cancel_result = TerminologyExtractionService(llm=_UnexpectedLlm()).extract(
                    (_text_evidence(evidence[0]),),
                    llm_enabled=True,
                    cancellation=_Cancelled(),
                )
                if not cancel_result.cancelled:
                    raise RuntimeError("production extraction cancellation boundary was not observed")
            repository.close()

        timings.extend((
            PhaseTiming(
                TerminologyPhase.EXTERNAL_LLM_WAIT,
                0.0,
                (("status", "disabled"), ("requests", 0), ("retries", 0)),
            ),
            PhaseTiming(TerminologyPhase.EXTERNAL_IO_WAIT, 0.0, (("status", "none-observed"),)),
        ))
        gc.collect()
        rss = _rss_or_none()
        timings.append(
            PhaseTiming(
                TerminologyPhase.CLEANUP,
                0.0,
                (("rss_end_bytes", rss), ("measurement", "psutil-rss-end-of-run")),
            )
        )
        runs.append(
            BenchmarkRun(
                iteration=iteration,
                timings=tuple(timings),
                peak_memory_bytes=None,
                memory_measurement=(
                    "peak-unavailable; psutil end-of-run RSS is retained in cleanup details"
                    if rss is not None
                    else "unavailable"
                ),
            )
        )
    return tuple(runs)


def _validate_real_adapters(dataset: Any, source_index: dict[str, Any]) -> None:
    adapters = {
        FormatId.PLUGIN_SSE: SsePluginAdapter(),
        FormatId.XML_EET: EetXmlAdapter(),
        FormatId.XML_XT: XtXmlAdapter(),
        FormatId.STRINGS: LocalizedStringsAdapter(FormatId.STRINGS),
        FormatId.JSON_PARATRANZ: ParatranzJsonAdapter(),
    }
    for template in source_index["templates"]:
        path = dataset.root / template["path"]
        format_id = FormatId(template["format_id"])
        parsed = adapters[format_id].parse(
            ParseRequest(
                SourceDescriptor(str(path), path.name, path.stat().st_size),
                RequestContext("terminology-production-benchmark", run_id=f"adapter-{format_id.value}"),
                format_id,
            )
        )
        if parsed.outcome not in {OperationOutcome.COMPLETED, OperationOutcome.PARTIAL}:
            raise RuntimeError(f"real adapter fixture failed: {format_id.value}")


def _evidence(row: dict[str, Any]) -> BilingualEvidence:
    return BilingualEvidence(
        row["evidence_id"],
        "benchmark-project",
        "main",
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


def _text_evidence(value: BilingualEvidence) -> BilingualEvidence:
    from dataclasses import replace

    return replace(value, context="BOOK:DESC")


def _decisions(candidates: tuple[Any, ...], conflicts: tuple[Any, ...]) -> tuple[TermDecision, ...]:
    conflicting = {item.normalized_original for item in conflicts}
    result = []
    for candidate in candidates:
        if candidate.normalized_original in conflicting:
            continue
        result.append(
            TermDecision(
                term_id=term_id(
                    project_id="benchmark-project",
                    variant_id="main",
                    scope=candidate.scope,
                    original=candidate.original,
                ),
                project_id="benchmark-project",
                variant_id="main",
                original=candidate.original,
                normalized_original=candidate.normalized_original,
                translation=candidate.translation,
                scope=candidate.scope,
                status=DecisionStatus.ADOPTED,
                evidence_ids=candidate.evidence_ids,
            )
        )
    return tuple(sorted(result, key=lambda item: item.term_id))


class _Cancelled:
    is_cancelled = True


class _UnexpectedLlm:
    def extract(self, batch: tuple[Any, ...]) -> tuple[Any, ...]:
        raise AssertionError("cancelled workload must not schedule a new LLM batch")


def _cache_preparation(scenario: str) -> dict[str, Any]:
    cold = scenario == "full-cold"
    warm = scenario in {"full-warm", "repeat"}
    preparation = {
        "project_result_cache": "fresh-repository-per-iteration",
        "python_object_cache": "gc-before-run" if cold else "not-controlled",
        "os_file_cache": "not-cleared-recorded-boundary",
        "antivirus_state": "operator-must-record-in-rc-notes",
        "network_paths": "excluded-from-contract-corpus",
        "formal_scenario_semantics": "not-implemented-diagnostic-smoke",
    }
    if warm:
        preparation["requested_warm_or_repeat_cache"] = "not-retained-fresh-repository-per-iteration"
    if scenario == "changed-10pct":
        preparation["change_policy"] = "first-floor(evidence-count*0.10)-translations"
        preparation["change_fraction_max"] = 0.10
        preparation["incremental_reuse"] = "not-executed-full-reduction-only"
        preparation["full_rebuild_digest_comparison"] = "not-executed"
    return preparation


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    parser.add_argument(
        "--profile",
        choices=("diagnostic", "formal"),
        default="diagnostic",
        help="Diagnostic smoke is never gate eligible; formal executes scenario-specific production semantics.",
    )
    parser.add_argument("--scale", choices=tuple(DATASET_SPECS), default="regular")
    parser.add_argument("--cpu-model")
    parser.add_argument("--physical-cores", type=int)
    parser.add_argument("--memory-bytes", type=int)
    parser.add_argument(
        "--disk-type",
        required=True,
        choices=("SSD", "NVMe", "SATA-SSD", "unknown"),
        help="Use 'unknown' for diagnostic smoke runs; those results can never satisfy the reference-device gate.",
    )
    parser.add_argument("--reference-device-id", default="local-reference")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def _default_output(profile: str, scale: str, scenario: str) -> Path:
    safe_build = "".join(character if character.isalnum() or character in ".-_" else "-" for character in __version__)
    date = __import__("datetime").date.today().isoformat()
    return (
        REPO_ROOT
        / "docs"
        / "test-reports"
        / "terminology-benchmarks"
        / "results"
        / f"{date}-{safe_build}"
        / f"{profile}-{scale}"
        / f"{scenario}.json"
    )


def _psutil_value(name: str, **kwargs: Any) -> Any:
    try:
        import psutil

        return getattr(psutil, name)(**kwargs)
    except Exception:
        return None


def _total_memory_bytes() -> int | None:
    try:
        import psutil

        return int(psutil.virtual_memory().total)
    except Exception:
        return None


def _windows_build_number() -> int:
    try:
        return int(platform.version().split(".")[-1])
    except (ValueError, IndexError):
        return 0


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _rss_or_none() -> int | None:
    try:
        return current_rss_bytes()
    except RuntimeError:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
