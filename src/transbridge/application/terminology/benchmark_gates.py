"""Fail-closed aggregation of FR5.16 formal benchmark evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Final

from .budgets import FR516_BUDGETS, BudgetComparator, BudgetLevel, TerminologyBudget
from .feature_gates import (
    TERMINOLOGY_GATE_CHECK_IDS,
    GateCheck,
    GateCheckStatus,
    TerminologyReleaseEvidence,
)

BUNDLE_SCHEMA_VERSION: Final = 1
FORMAL_MANIFEST_SCHEMA_VERSION: Final = 2
FORMAL_WORKLOAD: Final = "project-terminology-formal-v1"
FORMAL_PROFILE: Final = "formal-gate-v1"
FORMAL_SCALES: Final = ("regular", "stress")
FORMAL_SCENARIOS: Final = (
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


@dataclass(frozen=True, slots=True)
class BudgetObservation:
    value: int | float | bool | str | None
    statistic: str
    evidence: str


@dataclass(frozen=True, slots=True)
class BudgetCheckResult:
    budget: TerminologyBudget
    status: GateCheckStatus
    observation: BudgetObservation


@dataclass(frozen=True, slots=True)
class TerminologyBenchmarkBundleResult:
    evidence: TerminologyReleaseEvidence
    budget_checks: tuple[BudgetCheckResult, ...]
    diagnostics: tuple[str, ...]
    shall_passed: bool
    should_passed: bool


@dataclass(frozen=True, slots=True)
class TerminologyBenchmarkBundleArtifact:
    path: Path
    artifact_digest: str
    result: TerminologyBenchmarkBundleResult


class TerminologyBenchmarkBundleLoader:
    """Load a digest-bound bundle and every digest-bound scenario manifest."""

    def load(self, path: str | Path | None) -> TerminologyBenchmarkBundleResult:
        if path is None or not str(path).strip():
            return _failed_bundle("benchmark-bundle-path-not-configured")
        target = Path(path)
        try:
            bundle = json.loads(target.read_text(encoding="utf-8"))
            _validate_digest_bound_object(bundle, BUNDLE_SCHEMA_VERSION, "benchmark bundle")
            manifest_paths = bundle.get("manifests")
            if not isinstance(manifest_paths, list) or not manifest_paths:
                raise ValueError("benchmark bundle manifests must be a non-empty list")
            manifests = tuple(_load_manifest((target.parent / str(item)).resolve()) for item in manifest_paths)
            supplemental = bundle.get("supplemental_metrics", {})
            if not isinstance(supplemental, dict):
                raise ValueError("supplemental metrics must be an object")
            additional = _decode_additional_checks(bundle.get("additional_checks", []))
            return TerminologyBenchmarkBundleEvaluator().evaluate(manifests, supplemental, additional)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return _failed_bundle(f"benchmark-bundle-invalid:{type(exc).__name__}")


def write_benchmark_bundle(
    regular_directory: str | Path,
    stress_directory: str | Path,
    supplemental_evidence_path: str | Path,
    output: str | Path,
    *,
    overwrite: bool = False,
) -> TerminologyBenchmarkBundleArtifact:
    """Create one self-validating bundle from two complete scenario directories."""

    target = Path(output)
    if target.exists() and not overwrite:
        raise FileExistsError(f"benchmark bundle already exists: {target}")
    supplemental = json.loads(Path(supplemental_evidence_path).read_text(encoding="utf-8"))
    _validate_digest_bound_object(supplemental, BUNDLE_SCHEMA_VERSION, "supplemental evidence")
    metrics = supplemental.get("supplemental_metrics")
    additional = supplemental.get("additional_checks", [])
    if not isinstance(metrics, dict) or not isinstance(additional, list):
        raise ValueError("supplemental evidence requires metrics and additional checks")

    sources: list[Path] = []
    manifests: list[Mapping[str, Any]] = []
    for scale, directory in (("regular", Path(regular_directory)), ("stress", Path(stress_directory))):
        for scenario in FORMAL_SCENARIOS:
            source = directory / f"{scenario}.json"
            manifest = _load_manifest(source)
            _validate_manifest(manifest)
            if _path(manifest, "dataset", "spec", "name") != scale or manifest.get("scenario") != scenario:
                raise ValueError(f"formal manifest is in the wrong bundle slot: {source}")
            sources.append(source.resolve())
            manifests.append(manifest)
    decoded_additional = _decode_additional_checks(additional)
    preview = TerminologyBenchmarkBundleEvaluator().evaluate(manifests, metrics, decoded_additional)
    if preview.diagnostics:
        raise ValueError("benchmark bundle inputs are inconsistent: " + ";".join(preview.diagnostics))

    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "manifests": [Path(os.path.relpath(source, target.parent)).as_posix() for source in sources],
        "supplemental_metrics": metrics,
        "additional_checks": additional,
    }
    digest = _object_digest(payload)
    target.write_text(
        json.dumps({**payload, "artifact_digest": digest}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    loaded = TerminologyBenchmarkBundleLoader().load(target)
    if loaded.diagnostics:
        raise RuntimeError("generated benchmark bundle failed self-validation: " + ";".join(loaded.diagnostics))
    return TerminologyBenchmarkBundleArtifact(target, digest, loaded)


class TerminologyBenchmarkBundleEvaluator:
    """Project a complete regular+stress scenario bundle into production gate checks."""

    def evaluate(
        self,
        manifests: Sequence[Mapping[str, Any]],
        supplemental_metrics: Mapping[str, Any] | None = None,
        additional_checks: Sequence[GateCheck] = (),
    ) -> TerminologyBenchmarkBundleResult:
        supplemental = supplemental_metrics or {}
        diagnostics: list[str] = []
        indexed: dict[tuple[str, str], Mapping[str, Any]] = {}
        environment_identity: tuple[Any, ...] | None = None
        for manifest in manifests:
            try:
                _validate_manifest(manifest)
                scale = str(manifest["dataset"]["spec"]["name"])
                scenario = str(manifest["scenario"])
                key = (scale, scenario)
                if key in indexed:
                    raise ValueError(f"duplicate formal manifest:{scale}:{scenario}")
                identity = _environment_identity(manifest["environment"])
                if environment_identity is None:
                    environment_identity = identity
                elif identity != environment_identity:
                    raise ValueError("formal manifests were not captured on one environment/build")
                indexed[key] = manifest
            except (KeyError, TypeError, ValueError) as exc:
                diagnostics.append(f"manifest-invalid:{type(exc).__name__}:{exc}")

        expected = {(scale, scenario) for scale in FORMAL_SCALES for scenario in FORMAL_SCENARIOS}
        for scale, scenario in sorted(expected - set(indexed)):
            diagnostics.append(f"manifest-missing:{scale}:{scenario}")
        for scale, scenario in sorted(set(indexed) - expected):
            diagnostics.append(f"manifest-unexpected:{scale}:{scenario}")

        budget_results = tuple(_evaluate_budget(budget, indexed, supplemental) for budget in FR516_BUDGETS)
        shall_passed = not diagnostics and all(
            item.status is GateCheckStatus.PASSED for item in budget_results if item.budget.level is BudgetLevel.SHALL
        )
        should_passed = not diagnostics and all(
            item.status is GateCheckStatus.PASSED for item in budget_results if item.budget.level is BudgetLevel.SHOULD
        )
        generated = _generated_checks(indexed, budget_results, shall_passed, diagnostics)
        checks = _merge_checks(generated, additional_checks, diagnostics)
        return TerminologyBenchmarkBundleResult(
            TerminologyReleaseEvidence(checks),
            budget_results,
            tuple(diagnostics),
            shall_passed,
            should_passed,
        )


def _evaluate_budget(
    budget: TerminologyBudget,
    manifests: Mapping[tuple[str, str], Mapping[str, Any]],
    supplemental: Mapping[str, Any],
) -> BudgetCheckResult:
    observation = _observe(budget, manifests, supplemental)
    if observation.value is None:
        status = GateCheckStatus.NOT_RUN
    else:
        status = GateCheckStatus.PASSED if _compare(observation.value, budget) else GateCheckStatus.FAILED
    return BudgetCheckResult(budget, status, observation)


def _observe(
    budget: TerminologyBudget,
    manifests: Mapping[tuple[str, str], Mapping[str, Any]],
    supplemental: Mapping[str, Any],
) -> BudgetObservation:
    metric = budget.metric
    scale = budget.profile if budget.profile in FORMAL_SCALES else "regular"
    any_manifest = next(iter(manifests.values()), None)
    if metric in {"visible-feedback", "progress-heartbeat", "main-thread-block", "cancel-visible-feedback"}:
        return _supplemental(metric, supplemental)
    if metric == "windows-version":
        value = (
            None
            if any_manifest is None
            else "Windows 11"
            if any_manifest["environment"].get("windows", {}).get("build", 0) >= 22_000
            else "other"
        )
        return BudgetObservation(value, "exact", "environment.windows.build")
    if metric == "physical-cpu-cores":
        return BudgetObservation(_path(any_manifest, "environment", "physical_cores"), "exact", "environment")
    if metric == "memory":
        return BudgetObservation(_path(any_manifest, "environment", "memory_bytes"), "exact", "environment")
    if metric == "disk-media":
        value = _path(any_manifest, "environment", "disk_type")
        normalized = "SSD" if str(value).lower() in {"ssd", "nvme", "sata-ssd"} else value
        return BudgetObservation(normalized, "exact", "environment")
    dataset_names = {
        "source-count": "sources",
        "evidence-count": "evidence",
        "terminology-count": "terminology",
        "conflict-count": "conflict_groups",
        "history-count": "versions",
    }
    if metric in dataset_names:
        value = _path(manifests.get((scale, "full-cold")), "dataset", "expected_counts", dataset_names[metric])
        return BudgetObservation(value, "exact", f"{scale}.dataset.expected_counts")
    if metric == "local-build":
        return _max_summary(manifests.get((scale, "full-cold")), "build-local-total", "maximum-of-five-raw")
    if metric == "peak-additional-memory":
        return _max_runs(manifests.get((scale, "full-cold")), "peak_memory_bytes", "maximum-of-five-raw")
    if metric == "stable-growth-after-five-runs":
        values = [_path(item, "summary", "rss-recovered", "stable_growth_bytes") for item in manifests.values()]
        return _maximum(values, "maximum-across-all-scenarios", "summary.rss-recovered")
    if metric == "cancel-terminal":
        return _max_phase(manifests.get(("regular", "cancel")), "cancel")
    if metric == "exact-reuse":
        return _max_summary(manifests.get(("regular", "repeat")), "all-local-total", "maximum-of-five-raw")
    if metric == "incremental-changed-evidence":
        values = []
        for details in _phase_details(manifests.get(("regular", "changed-10pct")), "reduce"):
            changed, total = details.get("changed_evidence"), details.get("total_evidence")
            values.append(None if not total else float(changed) * 100.0 / float(total))
        return _maximum(values, "maximum-of-five-raw", "changed-10pct.reduce.details")
    if metric == "incremental-vs-full":
        return _supplemental(metric, supplemental)
    if metric == "incremental-digest-parity":
        item = manifests.get(("regular", "changed-10pct"))
        complete = _has_semantic(item, "incremental-full-rebuild-digest-parity")
        return BudgetObservation(complete if item is not None else None, "all-runs", "scenario semantics")
    phase_metrics = {
        "query-first-page": ("query", "query"),
        "history-open": ("history", "history"),
        "compare-summary": ("compare", "compare"),
        "quality-report": ("report", "report"),
        "changelog": ("changelog", "changelog"),
    }
    if metric in phase_metrics:
        scenario, phase = phase_metrics[metric]
        return _max_phase(manifests.get(("regular", scenario)), phase)
    if metric == "export-truncation":
        report = _phase_details(manifests.get(("regular", "report")), "report")
        changelog = _phase_details(manifests.get(("regular", "changelog")), "changelog")
        complete = (
            bool(report and changelog)
            and all(details.get("semantic_rows") == 55_000 for details in report)
            and all(
                details.get("semantic_changes") == 50_000 and details.get("semantic_manifest_parity") is True
                for details in changelog
            )
        )
        return BudgetObservation(False if complete else None, "all-runs", "renderer semantic manifests")
    if metric == "llm-wait-separated":
        complete = bool(manifests) and all(
            item.get("sampling", {}).get("llm_wait_excluded_from_local_duration") is True
            and all("external_llm_wait_seconds" in run for run in item.get("runs", ()))
            for item in manifests.values()
        )
        return BudgetObservation(complete if manifests else None, "all-runs", "sampling and run buckets")
    if metric == "llm-unbounded-retry":
        details = [value for item in manifests.values() for value in _phase_details(item, "external-llm-wait")]
        complete = bool(details) and all(value.get("retries") == 0 for value in details)
        return BudgetObservation(False if complete else None, "all-runs", "external-llm-wait details")
    if metric == "deterministic-skip-path":
        details = [value for item in manifests.values() for value in _phase_details(item, "external-llm-wait")]
        complete = bool(details) and all(value.get("status") == "disabled" for value in details)
        return BudgetObservation(complete if details else None, "all-runs", "external-llm-wait details")
    return BudgetObservation(None, "missing", "no observation rule")


def _generated_checks(indexed, results, shall_passed: bool, diagnostics: Sequence[str]) -> tuple[GateCheck, ...]:
    lookup = {item.budget.metric: item for item in results}
    complete = not diagnostics
    environment_metrics = ("windows-version", "physical-cpu-cores", "memory", "disk-media")
    checks = (
        _aggregate_check("reference-environment-calibrated", (lookup[item] for item in environment_metrics)),
        GateCheck(
            "regular-benchmark-complete",
            GateCheckStatus.PASSED
            if complete and all(("regular", item) in indexed for item in FORMAL_SCENARIOS)
            else GateCheckStatus.NOT_RUN,
        ),
        GateCheck(
            "stress-benchmark-complete",
            GateCheckStatus.PASSED
            if complete and all(("stress", item) in indexed for item in FORMAL_SCENARIOS)
            else GateCheckStatus.NOT_RUN,
        ),
        GateCheck("fr516-shall-budgets-passed", GateCheckStatus.PASSED if shall_passed else GateCheckStatus.FAILED),
        _aggregate_check("five-run-memory-stable", (lookup["stable-growth-after-five-runs"],)),
        _aggregate_check("cancel-response-passed", (lookup["cancel-visible-feedback"], lookup["cancel-terminal"])),
        _aggregate_check("incremental-digest-parity", (lookup["incremental-digest-parity"],)),
        _aggregate_check("quality-report-passed", (lookup["quality-report"], lookup["export-truncation"])),
        _aggregate_check("changelog-parity-passed", (lookup["changelog"], lookup["export-truncation"])),
    )
    return checks


def _aggregate_check(check_id: str, values) -> GateCheck:
    statuses = tuple(item.status for item in values)
    if statuses and all(item is GateCheckStatus.PASSED for item in statuses):
        status = GateCheckStatus.PASSED
    elif any(item is GateCheckStatus.FAILED for item in statuses):
        status = GateCheckStatus.FAILED
    else:
        status = GateCheckStatus.NOT_RUN
    return GateCheck(check_id, status)


def _merge_checks(generated, additional, diagnostics: list[str]) -> tuple[GateCheck, ...]:
    values = {item.check_id: item for item in generated}
    for item in additional:
        if item.check_id in values:
            diagnostics.append(f"additional-check-cannot-override-generated:{item.check_id}")
            continue
        values[item.check_id] = item
    return tuple(sorted(values.values(), key=lambda item: item.check_id))


def _validate_manifest(value: Mapping[str, Any]) -> None:
    _validate_digest_bound_object(value, FORMAL_MANIFEST_SCHEMA_VERSION, "formal manifest")
    if value.get("workload") != FORMAL_WORKLOAD or value.get("evidence", {}).get("profile") != FORMAL_PROFILE:
        raise ValueError("manifest is not formal benchmark evidence")
    if value.get("evidence", {}).get("gate_eligible") is not True:
        raise ValueError("single-scenario evidence contract is not eligible")
    if value.get("evidence", {}).get("eligibility_scope") != "single-scenario-evidence-contract-only":
        raise ValueError("manifest eligibility scope is missing")


def _load_manifest(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("formal manifest must be an object")
    return value


def _validate_digest_bound_object(value: Any, schema: int, label: str) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != schema:
        raise ValueError(f"invalid {label} schema")
    digest = value.get("artifact_digest")
    unsigned = {key: item for key, item in value.items() if key != "artifact_digest"}
    if digest != _object_digest(unsigned):
        raise ValueError(f"{label} digest mismatch")


def _object_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _environment_identity(value: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        value.get("reference_device_id"),
        value.get("cpu_model"),
        value.get("physical_cores"),
        value.get("memory_bytes"),
        value.get("disk_type"),
        value.get("python"),
        value.get("transbridge_build"),
        value.get("windows", {}).get("build"),
    )


def _supplemental(metric: str, values: Mapping[str, Any]) -> BudgetObservation:
    item = values.get(metric)
    if not isinstance(item, dict) or "value" not in item or not str(item.get("evidence", "")).strip():
        return BudgetObservation(None, "missing", f"supplemental_metrics.{metric}")
    return BudgetObservation(item["value"], str(item.get("statistic", "maximum-raw")), str(item["evidence"]))


def _compare(value: Any, budget: TerminologyBudget) -> bool:
    try:
        if budget.comparator is BudgetComparator.AT_MOST:
            return float(value) <= float(budget.limit)
        if budget.comparator is BudgetComparator.AT_LEAST:
            return float(value) >= float(budget.limit)
        if budget.comparator is BudgetComparator.EQUALS:
            return value == budget.limit
        if budget.comparator is BudgetComparator.REQUIRED:
            return value == budget.limit
    except (TypeError, ValueError):
        return False
    return False


def _max_summary(manifest, key: str, statistic: str) -> BudgetObservation:
    return _maximum(_path(manifest, "summary", key, "raw_samples") or (), statistic, f"summary.{key}.raw_samples")


def _max_runs(manifest, key: str, statistic: str) -> BudgetObservation:
    values = () if manifest is None else [item.get(key) for item in manifest.get("runs", ())]
    return _maximum(values, statistic, f"runs.{key}")


def _max_phase(manifest, phase: str) -> BudgetObservation:
    values = [
        item.get("duration_seconds")
        for run in (() if manifest is None else manifest.get("runs", ()))
        for item in run.get("timings", ())
        if item.get("phase") == phase
    ]
    return _maximum(values, "maximum-of-five-raw", f"runs.timings.{phase}")


def _phase_details(manifest, phase: str) -> list[Mapping[str, Any]]:
    if manifest is None:
        return []
    return [
        item.get("details", {})
        for run in manifest.get("runs", ())
        for item in run.get("timings", ())
        if item.get("phase") == phase
    ]


def _maximum(values, statistic: str, evidence: str) -> BudgetObservation:
    present = [item for item in values if isinstance(item, (int, float)) and not isinstance(item, bool)]
    return BudgetObservation(max(present) if present and len(present) == len(values) else None, statistic, evidence)


def _path(value: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _has_semantic(manifest, semantic: str) -> bool:
    return manifest is not None and semantic in manifest.get("evidence", {}).get("scenario_contract", {}).get(
        "completed_semantics", ()
    )


def _decode_additional_checks(value: Any) -> tuple[GateCheck, ...]:
    if not isinstance(value, list):
        raise ValueError("additional checks must be a list")
    checks: list[GateCheck] = []
    for item in value:
        if not isinstance(item, dict) or set(item) - {"check_id", "status", "detail"}:
            raise ValueError("invalid additional release check")
        check_id = str(item.get("check_id", ""))
        if check_id not in TERMINOLOGY_GATE_CHECK_IDS:
            raise ValueError(f"unknown additional release check: {check_id}")
        checks.append(GateCheck(check_id, GateCheckStatus(item.get("status")), str(item.get("detail", ""))))
    return tuple(checks)


def _failed_bundle(diagnostic: str) -> TerminologyBenchmarkBundleResult:
    return TerminologyBenchmarkBundleResult(TerminologyReleaseEvidence(), (), (diagnostic,), False, False)


__all__ = [
    "BUNDLE_SCHEMA_VERSION",
    "BudgetCheckResult",
    "BudgetObservation",
    "FORMAL_SCALES",
    "FORMAL_SCENARIOS",
    "TerminologyBenchmarkBundleArtifact",
    "TerminologyBenchmarkBundleEvaluator",
    "TerminologyBenchmarkBundleLoader",
    "TerminologyBenchmarkBundleResult",
    "write_benchmark_bundle",
]
