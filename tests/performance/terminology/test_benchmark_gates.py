from __future__ import annotations

import hashlib
import json
from pathlib import Path

from transbridge.application.terminology.benchmark_gates import (
    FORMAL_SCALES,
    FORMAL_SCENARIOS,
    TerminologyBenchmarkBundleEvaluator,
    TerminologyBenchmarkBundleLoader,
    write_benchmark_bundle,
)
from transbridge.application.terminology.feature_gates import GateCheckStatus


def _digest(payload):
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manifest(scale: str, scenario: str):
    counts = {
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
    }[scale]
    phase = {
        "repeat": "query",
        "changed-10pct": "reduce",
        "query": "query",
        "history": "history",
        "compare": "compare",
        "report": "report",
        "changelog": "changelog",
        "cancel": "cancel",
    }.get(scenario, "capture")
    details = {}
    if scenario == "changed-10pct":
        details = {"changed_evidence": counts["evidence"] // 10, "total_evidence": counts["evidence"]}
    elif scenario == "report":
        details = {"semantic_rows": counts["terminology"] + counts["conflict_groups"]}
    elif scenario == "changelog":
        details = {"semantic_changes": counts["terminology"], "semantic_manifest_parity": True}
    runs = [
        {
            "iteration": index,
            "timings": [
                {"phase": phase, "duration_seconds": 0.01, "details": details},
                {
                    "phase": "external-llm-wait",
                    "duration_seconds": 0.0,
                    "details": {"status": "disabled", "retries": 0},
                },
            ],
            "build_duration_seconds": 1.0,
            "external_llm_wait_seconds": 0.0,
            "external_io_wait_seconds": 0.0,
            "peak_memory_bytes": 1024,
        }
        for index in range(1, 6)
    ]
    payload = {
        "schema_version": 2,
        "workload": "project-terminology-formal-v1",
        "scenario": scenario,
        "environment": {
            "reference_device_id": "win11-ref",
            "cpu_model": "cpu",
            "physical_cores": 4,
            "memory_bytes": 16 * 1024**3,
            "disk_type": "NVMe",
            "python": "3.12",
            "transbridge_build": "test",
            "windows": {"build": 22_000},
        },
        "dataset": {"spec": {"name": scale}, "expected_counts": counts},
        "sampling": {"llm_wait_excluded_from_local_duration": True},
        "runs": runs,
        "summary": {
            "build-local-total": {"raw_samples": [1.0] * 5},
            "all-local-total": {"raw_samples": [0.01] * 5},
            "rss-recovered": {"stable_growth_bytes": 0},
        },
        "evidence": {
            "profile": "formal-gate-v1",
            "gate_eligible": True,
            "eligibility_scope": "single-scenario-evidence-contract-only",
            "release_gate_eligible": False,
            "scenario_contract": {
                "completed_semantics": ["incremental-full-rebuild-digest-parity"] if scenario == "changed-10pct" else []
            },
        },
    }
    return {**payload, "artifact_digest": _digest(payload)}


def _supplemental():
    return {
        metric: {"value": value, "statistic": "maximum-raw", "evidence": f"qa:{metric}"}
        for metric, value in {
            "visible-feedback": 0.1,
            "progress-heartbeat": 1.0,
            "main-thread-block": 0.1,
            "cancel-visible-feedback": 0.1,
            "incremental-vs-full": 10.0,
        }.items()
    }


def test_incomplete_bundle_is_fail_closed_and_cannot_pass_shall_budgets() -> None:
    result = TerminologyBenchmarkBundleEvaluator().evaluate((_manifest("regular", "query"),), _supplemental())

    assert result.shall_passed is False
    assert any(item.startswith("manifest-missing:stress") for item in result.diagnostics)
    assert result.evidence.status("fr516-shall-budgets-passed") is GateCheckStatus.FAILED


def test_complete_regular_and_stress_bundle_projects_only_passing_budget_checks() -> None:
    manifests = tuple(_manifest(scale, scenario) for scale in FORMAL_SCALES for scenario in FORMAL_SCENARIOS)

    result = TerminologyBenchmarkBundleEvaluator().evaluate(manifests, _supplemental())

    assert result.diagnostics == ()
    assert result.shall_passed is True
    assert result.should_passed is True
    assert result.evidence.status("regular-benchmark-complete") is GateCheckStatus.PASSED
    assert result.evidence.status("stress-benchmark-complete") is GateCheckStatus.PASSED
    assert result.evidence.status("fr516-shall-budgets-passed") is GateCheckStatus.PASSED
    assert all(item.observation.statistic for item in result.budget_checks)


def test_missing_ui_or_renderer_evidence_is_not_treated_as_zero_or_passed() -> None:
    manifests = tuple(_manifest(scale, scenario) for scale in FORMAL_SCALES for scenario in FORMAL_SCENARIOS)
    manifests = tuple(
        {
            **item,
            "runs": [
                {**run, "timings": [timing for timing in run["timings"] if timing["phase"] != "report"]}
                for run in item["runs"]
            ],
        }
        if item["scenario"] == "report" and item["dataset"]["spec"]["name"] == "regular"
        else item
        for item in manifests
    )
    supplemental = _supplemental()
    supplemental.pop("visible-feedback")

    result = TerminologyBenchmarkBundleEvaluator().evaluate(manifests, supplemental)

    assert result.shall_passed is False
    assert next(item for item in result.budget_checks if item.budget.metric == "visible-feedback").status is (
        GateCheckStatus.NOT_RUN
    )
    assert next(item for item in result.budget_checks if item.budget.metric == "export-truncation").status is (
        GateCheckStatus.NOT_RUN
    )


def test_digest_bound_bundle_loader_resolves_all_manifests_and_rejects_tampering(tmp_path: Path) -> None:
    for scale in FORMAL_SCALES:
        for scenario in FORMAL_SCENARIOS:
            target = tmp_path / scale / f"{scenario}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(_manifest(scale, scenario)), encoding="utf-8")
    supplemental_payload = {
        "schema_version": 1,
        "supplemental_metrics": _supplemental(),
        "additional_checks": [],
    }
    supplemental = tmp_path / "supplemental.json"
    supplemental.write_text(
        json.dumps({**supplemental_payload, "artifact_digest": _digest(supplemental_payload)}),
        encoding="utf-8",
    )
    bundle = tmp_path / "bundle.json"

    artifact = write_benchmark_bundle(tmp_path / "regular", tmp_path / "stress", supplemental, bundle)

    loaded = TerminologyBenchmarkBundleLoader().load(bundle)
    assert artifact.artifact_digest == json.loads(bundle.read_text(encoding="utf-8"))["artifact_digest"]
    assert loaded.shall_passed is True

    tampered = json.loads(bundle.read_text(encoding="utf-8"))
    tampered["supplemental_metrics"]["visible-feedback"]["value"] = 99
    bundle.write_text(json.dumps(tampered), encoding="utf-8")
    rejected = TerminologyBenchmarkBundleLoader().load(bundle)
    assert rejected.evidence.checks == ()
    assert rejected.diagnostics == ("benchmark-bundle-invalid:ValueError",)
