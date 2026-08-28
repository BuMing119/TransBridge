"""Phase-aware measurement and manifest contracts for terminology benchmarks."""

from __future__ import annotations

from collections.abc import Iterator, MutableSequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import time
from typing import Any

from tests.performance.measure import summarize

BENCHMARK_MANIFEST_SCHEMA_VERSION = 2
FORMAL_REPETITIONS = 5
FORMAL_WORKLOAD = "project-terminology-formal-v1"
FORMAL_DATASET_SCALES = frozenset({"regular", "stress"})
FORMAL_DATASET_COUNTS = {
    "regular": {
        "sources": 50,
        "evidence": 250_000,
        "candidates": 55_000,
        "terminology": 50_000,
        "conflict_groups": 5_000,
        "versions": 10,
    },
    "stress": {
        "sources": 200,
        "evidence": 1_000_000,
        "candidates": 220_000,
        "terminology": 200_000,
        "conflict_groups": 20_000,
        "versions": 50,
    },
}
SCENARIOS = (
    "full-cold",
    "full-warm",
    "repeat",
    "changed-10pct",
    "query",
    "history",
    "compare",
    "report",
    "changelog",
    "cancel",
)


class BenchmarkEvidenceProfile(StrEnum):
    DIAGNOSTIC_SMOKE = "diagnostic-smoke-v1"
    FORMAL_GATE = "formal-gate-v1"


class TerminologyPhase(StrEnum):
    CAPTURE = "capture"
    PARSE = "parse"
    ASSEMBLE = "assemble"
    EXTRACT = "extract"
    REDUCE = "reduce"
    PERSIST = "persist"
    QUERY = "query"
    HISTORY = "history"
    COMPARE = "compare"
    REPORT = "report"
    CHANGELOG = "changelog"
    CANCEL = "cancel"
    CLEANUP = "cleanup"
    EXTERNAL_LLM_WAIT = "external-llm-wait"
    EXTERNAL_IO_WAIT = "external-io-wait"

    @property
    def is_external_wait(self) -> bool:
        return self in {self.EXTERNAL_LLM_WAIT, self.EXTERNAL_IO_WAIT}


BUILD_PHASES = frozenset({
    TerminologyPhase.CAPTURE,
    TerminologyPhase.PARSE,
    TerminologyPhase.ASSEMBLE,
    TerminologyPhase.EXTRACT,
    TerminologyPhase.REDUCE,
    TerminologyPhase.PERSIST,
})

SCENARIO_REQUIRED_PHASES = {
    "full-cold": BUILD_PHASES
    | {TerminologyPhase.EXTERNAL_LLM_WAIT, TerminologyPhase.EXTERNAL_IO_WAIT, TerminologyPhase.CLEANUP},
    "full-warm": BUILD_PHASES
    | {TerminologyPhase.EXTERNAL_LLM_WAIT, TerminologyPhase.EXTERNAL_IO_WAIT, TerminologyPhase.CLEANUP},
    "repeat": {
        TerminologyPhase.CAPTURE,
        TerminologyPhase.QUERY,
        TerminologyPhase.EXTERNAL_LLM_WAIT,
        TerminologyPhase.EXTERNAL_IO_WAIT,
        TerminologyPhase.CLEANUP,
    },
    "changed-10pct": BUILD_PHASES
    | {TerminologyPhase.EXTERNAL_LLM_WAIT, TerminologyPhase.EXTERNAL_IO_WAIT, TerminologyPhase.CLEANUP},
    "query": {TerminologyPhase.QUERY, TerminologyPhase.CLEANUP},
    "history": {TerminologyPhase.HISTORY, TerminologyPhase.CLEANUP},
    "compare": {TerminologyPhase.HISTORY, TerminologyPhase.COMPARE, TerminologyPhase.CLEANUP},
    "report": {TerminologyPhase.REPORT, TerminologyPhase.CLEANUP},
    "changelog": {TerminologyPhase.CHANGELOG, TerminologyPhase.CLEANUP},
    "cancel": {TerminologyPhase.CANCEL, TerminologyPhase.CLEANUP},
}

SCENARIO_REQUIRED_SEMANTICS = {
    "full-cold": ("cold-cache-boundary", "complete-production-build"),
    "full-warm": (
        "warmup-excluded-from-five-formal-samples",
        "warm-cache-retained-across-runs",
        "complete-production-build",
    ),
    "repeat": (
        "existing-result-prepared-before-five-formal-samples",
        "unchanged-input-result-reused",
        "no-full-reparse-or-llm-reschedule",
    ),
    "changed-10pct": (
        "baseline-prepared-before-five-formal-samples",
        "changed-evidence-at-most-10-percent",
        "incremental-reuse-and-recompute-counts-recorded",
        "incremental-full-rebuild-digest-parity",
    ),
    "query": ("full-scale-search-filter-sort-first-page",),
    "history": ("persisted-version-history-first-page",),
    "compare": ("two-persisted-versions-compared", "progressive-detail-load"),
    "report": ("quality-excel-renderer", "format-capacity-and-no-truncation"),
    "changelog": ("markdown-and-excel-renderers", "format-capacity-and-no-truncation"),
    "cancel": ("production-cancellation-boundary", "visible-cancel-terminal-within-three-seconds"),
}


@dataclass(frozen=True, slots=True)
class PhaseTiming:
    phase: TerminologyPhase
    duration_seconds: float
    details: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.duration_seconds < 0:
            raise ValueError("phase duration must not be negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "duration_seconds": self.duration_seconds,
            "external_wait": self.phase.is_external_wait,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class BenchmarkRun:
    iteration: int
    timings: tuple[PhaseTiming, ...]
    peak_memory_bytes: int | None = None
    memory_measurement: str | None = None
    rss_baseline_bytes: int | None = None
    rss_peak_bytes: int | None = None
    rss_recovered_bytes: int | None = None

    def __post_init__(self) -> None:
        if self.iteration < 1:
            raise ValueError("benchmark run iteration starts at one")
        if not self.timings:
            raise ValueError("benchmark run requires at least one phase timing")
        if self.peak_memory_bytes is not None and self.peak_memory_bytes < 0:
            raise ValueError("peak memory must not be negative")
        for value, label in (
            (self.rss_baseline_bytes, "RSS baseline"),
            (self.rss_peak_bytes, "RSS peak"),
            (self.rss_recovered_bytes, "RSS recovered"),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{label} must not be negative")
        if self.rss_peak_bytes is not None and self.rss_baseline_bytes is not None:
            additional = max(0, self.rss_peak_bytes - self.rss_baseline_bytes)
            if self.peak_memory_bytes is not None and self.peak_memory_bytes != additional:
                raise ValueError("peak memory must equal peak additional RSS when RSS samples are provided")

    @property
    def local_duration_seconds(self) -> float:
        return sum(timing.duration_seconds for timing in self.timings if not timing.phase.is_external_wait)

    @property
    def build_duration_seconds(self) -> float:
        return sum(timing.duration_seconds for timing in self.timings if timing.phase in BUILD_PHASES)

    @property
    def external_llm_wait_seconds(self) -> float:
        return sum(
            timing.duration_seconds for timing in self.timings if timing.phase is TerminologyPhase.EXTERNAL_LLM_WAIT
        )

    @property
    def external_io_wait_seconds(self) -> float:
        return sum(
            timing.duration_seconds for timing in self.timings if timing.phase is TerminologyPhase.EXTERNAL_IO_WAIT
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "timings": [timing.to_dict() for timing in self.timings],
            "local_duration_seconds": self.local_duration_seconds,
            "build_duration_seconds": self.build_duration_seconds,
            "external_llm_wait_seconds": self.external_llm_wait_seconds,
            "external_io_wait_seconds": self.external_io_wait_seconds,
            "peak_memory_bytes": self.peak_memory_bytes,
            "memory_measurement": self.memory_measurement,
            "rss_baseline_bytes": self.rss_baseline_bytes,
            "rss_peak_bytes": self.rss_peak_bytes,
            "rss_recovered_bytes": self.rss_recovered_bytes,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkManifest:
    scenario: str
    environment: dict[str, Any]
    dataset: dict[str, Any]
    cache_preparation: dict[str, Any]
    runs: tuple[BenchmarkRun, ...]
    workload: str = "project-terminology"
    evidence_profile: BenchmarkEvidenceProfile = BenchmarkEvidenceProfile.DIAGNOSTIC_SMOKE
    completed_semantics: tuple[str, ...] = ()
    known_limitations: tuple[str, ...] = ()
    schema_version: int = BENCHMARK_MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != BENCHMARK_MANIFEST_SCHEMA_VERSION:
            raise ValueError(f"unsupported benchmark manifest schema: {self.schema_version}")
        if self.scenario not in SCENARIOS:
            raise ValueError(f"unknown terminology benchmark scenario: {self.scenario}")
        if len(self.runs) != FORMAL_REPETITIONS:
            raise ValueError(f"benchmark evidence requires exactly {FORMAL_REPETITIONS} runs")
        if tuple(run.iteration for run in self.runs) != tuple(range(1, FORMAL_REPETITIONS + 1)):
            raise ValueError("benchmark iterations must be contiguous and one-based")
        if not isinstance(self.evidence_profile, BenchmarkEvidenceProfile):
            raise ValueError("benchmark evidence profile must use BenchmarkEvidenceProfile")

    @property
    def artifact_digest(self) -> str:
        return _canonical_digest(self._payload())

    def to_dict(self) -> dict[str, Any]:
        payload = self._payload()
        return {**payload, "artifact_digest": _canonical_digest(payload)}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def _payload(self) -> dict[str, Any]:
        evidence = self._evidence()
        return {
            "schema_version": self.schema_version,
            "workload": self.workload,
            "scenario": self.scenario,
            "environment": self.environment,
            "dataset": self.dataset,
            "cache_preparation": self.cache_preparation,
            "evidence": evidence,
            "sampling": {
                "clock": "time.perf_counter",
                "repetitions": FORMAL_REPETITIONS,
                "raw_samples_retained": True,
                "llm_wait_excluded_from_local_duration": True,
                "external_io_wait_excluded_from_local_duration": True,
            },
            "runs": [run.to_dict() for run in self.runs],
            "summary": _summarize_runs(self.runs),
        }

    def _evidence(self) -> dict[str, Any]:
        required_phases = SCENARIO_REQUIRED_PHASES[self.scenario]
        missing_phases: dict[str, list[str]] = {}
        duplicate_phases: dict[str, list[str]] = {}
        observed_by_run: dict[str, list[str]] = {}
        reasons: list[str] = []
        for run in self.runs:
            counts = {phase: sum(timing.phase is phase for timing in run.timings) for phase in TerminologyPhase}
            observed = {phase for phase, count in counts.items() if count}
            key = f"iteration-{run.iteration}"
            observed_by_run[key] = sorted(phase.value for phase in observed)
            missing = sorted(phase.value for phase in required_phases - observed)
            duplicates = sorted(phase.value for phase, count in counts.items() if count > 1)
            if missing:
                missing_phases[key] = missing
                reasons.extend(f"missing-required-phase:{key}:{phase}" for phase in missing)
            if duplicates:
                duplicate_phases[key] = duplicates
                reasons.extend(f"duplicate-phase:{key}:{phase}" for phase in duplicates)
            if run.peak_memory_bytes is None:
                reasons.append(f"peak-memory-not-measured:{key}")
            if self.evidence_profile is BenchmarkEvidenceProfile.FORMAL_GATE:
                if run.rss_baseline_bytes is None:
                    reasons.append(f"rss-baseline-not-measured:{key}")
                if run.rss_peak_bytes is None:
                    reasons.append(f"rss-peak-not-measured:{key}")
                if run.rss_recovered_bytes is None:
                    reasons.append(f"rss-recovered-not-measured:{key}")

        required_semantics = SCENARIO_REQUIRED_SEMANTICS[self.scenario]
        completed = set(self.completed_semantics)
        missing_semantics = [item for item in required_semantics if item not in completed]
        reasons.extend(f"missing-required-semantic:{item}" for item in missing_semantics)
        if self.evidence_profile is not BenchmarkEvidenceProfile.FORMAL_GATE:
            reasons.append(f"evidence-profile-not-formal:{self.evidence_profile.value}")
        if self.workload != FORMAL_WORKLOAD:
            reasons.append(f"workload-not-formal:{self.workload}")
        reasons.extend(_reference_environment_reasons(self.environment))
        dataset_scale = self.dataset.get("spec", {}).get("name")
        if dataset_scale not in FORMAL_DATASET_SCALES:
            reasons.append(f"dataset-scale-not-formal:{dataset_scale or 'unknown'}")
        else:
            reasons.extend(_formal_dataset_reasons(self.dataset, dataset_scale))
        reasons.extend(f"declared-limitation:{item}" for item in self.known_limitations)
        if self.evidence_profile is BenchmarkEvidenceProfile.FORMAL_GATE and not _has_five_run_memory_growth(self.runs):
            reasons.append("five-run-memory-growth-incomplete")
        unique_reasons = list(dict.fromkeys(reasons))
        return {
            "profile": self.evidence_profile.value,
            "gate_eligible": not unique_reasons,
            "eligibility_scope": "single-scenario-evidence-contract-only",
            "release_gate_eligible": False,
            "gate_ineligibility_reasons": unique_reasons,
            "scenario_contract": {
                "required_phases": sorted(phase.value for phase in required_phases),
                "observed_phases_by_run": observed_by_run,
                "missing_required_phases_by_run": missing_phases,
                "duplicate_phases_by_run": duplicate_phases,
                "required_semantics": list(required_semantics),
                "completed_semantics": sorted(completed),
                "missing_required_semantics": missing_semantics,
            },
        }


@contextmanager
def measure_phase(
    phase: TerminologyPhase,
    timings: MutableSequence[PhaseTiming],
    *,
    details: tuple[tuple[str, Any], ...] = (),
) -> Iterator[None]:
    """Measure a named phase and append it even when the workload raises."""

    start = time.perf_counter()
    try:
        yield
    finally:
        timings.append(PhaseTiming(phase, time.perf_counter() - start, details))


def _summarize_runs(runs: tuple[BenchmarkRun, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for phase in TerminologyPhase:
        per_run = [tuple(timing.duration_seconds for timing in run.timings if timing.phase is phase) for run in runs]
        if not all(len(samples) == 1 for samples in per_run):
            continue
        samples = [values[0] for values in per_run]
        result[phase.value] = {"raw_samples": samples, **summarize(samples, unit="s")}
    local_samples = [run.local_duration_seconds for run in runs]
    result["all-local-total"] = {"raw_samples": local_samples, **summarize(local_samples, unit="s")}
    build_phase_counts = [
        {phase: sum(timing.phase is phase for timing in run.timings) for phase in BUILD_PHASES} for run in runs
    ]
    if all(all(count == 1 for count in counts.values()) for counts in build_phase_counts):
        build_samples = [run.build_duration_seconds for run in runs]
        result["build-local-total"] = {"raw_samples": build_samples, **summarize(build_samples, unit="s")}
    recovered = [run.rss_recovered_bytes for run in runs]
    if all(value is not None for value in recovered):
        recovered_samples = [int(value) for value in recovered if value is not None]
        growth = [value - recovered_samples[0] for value in recovered_samples]
        result["rss-recovered"] = {
            "raw_samples": recovered_samples,
            "growth_from_first_bytes": growth,
            "stable_growth_bytes": max(growth),
            **summarize(recovered_samples, unit="bytes"),
        }
    additional = [run.peak_memory_bytes for run in runs]
    if all(value is not None for value in additional):
        samples = [int(value) for value in additional if value is not None]
        result["peak-additional-rss"] = {"raw_samples": samples, **summarize(samples, unit="bytes")}
    return result


def _has_five_run_memory_growth(runs: tuple[BenchmarkRun, ...]) -> bool:
    return len(runs) == FORMAL_REPETITIONS and all(
        run.rss_baseline_bytes is not None
        and run.rss_peak_bytes is not None
        and run.rss_recovered_bytes is not None
        and run.peak_memory_bytes is not None
        for run in runs
    )


def _canonical_digest(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _reference_environment_reasons(environment: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    windows = environment.get("windows", {})
    disk_type = str(environment.get("disk_type", "")).lower()
    required_fields = (
        environment.get("reference_device_id"),
        environment.get("cpu_model"),
        environment.get("python"),
        environment.get("transbridge_build"),
    )
    measurements = environment.get("measurement_tools", {})
    if environment.get("reference_requirements_met") is not True:
        reasons.append("reference-environment-not-calibrated")
    if not all(isinstance(value, str) and value.strip() for value in required_fields):
        reasons.append("reference-environment-fields-incomplete")
    if not isinstance(windows, dict) or not _integer_at_least(windows.get("build"), 22_000):
        reasons.append("reference-environment-not-windows-11")
    if not _integer_at_least(environment.get("physical_cores"), 4):
        reasons.append("reference-environment-insufficient-physical-cores")
    if not _integer_at_least(environment.get("memory_bytes"), 16 * 1024**3):
        reasons.append("reference-environment-insufficient-memory")
    if disk_type not in {"ssd", "nvme", "sata-ssd"}:
        reasons.append("reference-environment-disk-not-calibrated-ssd")
    if not isinstance(measurements, dict) or measurements.get("clock") != "time.perf_counter":
        reasons.append("reference-environment-clock-not-perf-counter")
    return reasons


def _formal_dataset_reasons(dataset: dict[str, Any], scale: str) -> list[str]:
    reasons: list[str] = []
    if dataset.get("expected_counts") != FORMAL_DATASET_COUNTS[scale]:
        reasons.append(f"dataset-counts-do-not-match:{scale}")
    if not _is_sha256(dataset.get("canonical_digest")):
        reasons.append("dataset-canonical-digest-missing-or-invalid")
    adapters = dataset.get("adapter_templates")
    if not isinstance(adapters, list) or len(adapters) != 5:
        reasons.append("dataset-adapter-manifest-must-contain-five-formats")
    elif any(
        not isinstance(item, dict)
        or not item.get("format_id")
        or not item.get("adapter_id")
        or not item.get("adapter_version")
        or not _is_sha256(item.get("sha256"))
        for item in adapters
    ):
        reasons.append("dataset-adapter-manifest-incomplete")
    elif len({item["format_id"] for item in adapters}) != 5:
        reasons.append("dataset-adapter-formats-must-be-distinct")
    return reasons


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _integer_at_least(value: Any, minimum: int) -> bool:
    try:
        return int(value) >= minimum
    except (TypeError, ValueError):
        return False
