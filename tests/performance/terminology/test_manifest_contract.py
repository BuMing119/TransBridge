from __future__ import annotations

import json

import pytest

from .measure import (
    BENCHMARK_MANIFEST_SCHEMA_VERSION,
    FORMAL_REPETITIONS,
    FORMAL_WORKLOAD,
    SCENARIO_REQUIRED_SEMANTICS,
    BenchmarkEvidenceProfile,
    BenchmarkManifest,
    BenchmarkRun,
    PhaseTiming,
    TerminologyPhase,
    measure_phase,
)


def _runs() -> tuple[BenchmarkRun, ...]:
    return tuple(
        BenchmarkRun(
            iteration=index,
            timings=(
                PhaseTiming(TerminologyPhase.CAPTURE, index / 100),
                PhaseTiming(TerminologyPhase.PARSE, index / 10),
                PhaseTiming(TerminologyPhase.EXTERNAL_LLM_WAIT, index * 2.0),
                PhaseTiming(TerminologyPhase.EXTERNAL_IO_WAIT, index * 3.0),
            ),
            peak_memory_bytes=index * 1024,
            memory_measurement="psutil-rss",
        )
        for index in range(1, FORMAL_REPETITIONS + 1)
    )


def _manifest() -> BenchmarkManifest:
    return BenchmarkManifest(
        scenario="full-cold",
        environment={"python": "3.12", "transbridge": "test-build"},
        dataset={"seed": 516_000, "canonical_digest": "a" * 64},
        cache_preparation={"project_cache": "cleared", "os_file_cache": "recorded-not-cleared"},
        runs=_runs(),
    )


def test_manifest_retains_five_raw_samples_only_for_observed_phase_buckets() -> None:
    payload = _manifest().to_dict()

    assert payload["schema_version"] == BENCHMARK_MANIFEST_SCHEMA_VERSION == 2
    assert payload["sampling"]["repetitions"] == 5
    assert len(payload["runs"]) == 5
    assert set(payload["summary"]) == {
        "capture",
        "parse",
        "external-llm-wait",
        "external-io-wait",
        "all-local-total",
        "peak-additional-rss",
    }
    assert payload["summary"]["parse"]["raw_samples"] == pytest.approx([0.1, 0.2, 0.3, 0.4, 0.5])
    assert payload["runs"][0]["local_duration_seconds"] == pytest.approx(0.11)
    assert payload["runs"][0]["build_duration_seconds"] == pytest.approx(0.11)
    assert payload["runs"][0]["external_llm_wait_seconds"] == 2.0
    assert payload["runs"][0]["external_io_wait_seconds"] == 3.0
    assert payload["evidence"]["gate_eligible"] is False
    assert payload["evidence"]["profile"] == "diagnostic-smoke-v1"
    assert payload["evidence"]["eligibility_scope"] == "single-scenario-evidence-contract-only"
    assert payload["evidence"]["release_gate_eligible"] is False
    assert "assemble" in payload["evidence"]["scenario_contract"]["missing_required_phases_by_run"]["iteration-1"]


def test_manifest_json_and_artifact_digest_are_stable() -> None:
    first = _manifest()
    second = _manifest()

    assert first.to_json() == second.to_json()
    assert first.artifact_digest == second.artifact_digest
    assert json.loads(first.to_json())["artifact_digest"] == first.artifact_digest


def test_manifest_rejects_missing_repetitions() -> None:
    with pytest.raises(ValueError, match="exactly 5"):
        BenchmarkManifest(
            scenario="full-cold",
            environment={},
            dataset={},
            cache_preparation={},
            runs=_runs()[:-1],
        )


def test_missing_phase_is_explicit_and_never_summarized_as_zero_seconds() -> None:
    runs = list(_runs())
    runs[-1] = BenchmarkRun(
        iteration=FORMAL_REPETITIONS,
        timings=tuple(timing for timing in runs[-1].timings if timing.phase is not TerminologyPhase.PARSE),
        peak_memory_bytes=runs[-1].peak_memory_bytes,
        memory_measurement=runs[-1].memory_measurement,
    )

    payload = BenchmarkManifest(
        scenario="full-cold",
        environment={},
        dataset={},
        cache_preparation={},
        runs=tuple(runs),
    ).to_dict()

    assert "parse" not in payload["summary"]
    assert "build-local-total" not in payload["summary"]
    assert "parse" in payload["evidence"]["scenario_contract"]["missing_required_phases_by_run"]["iteration-5"]
    assert "missing-required-phase:iteration-5:parse" in payload["evidence"]["gate_ineligibility_reasons"]


def test_observed_disabled_external_wait_is_retained_as_real_zero_sample() -> None:
    runs = tuple(
        BenchmarkRun(
            iteration=index,
            timings=(
                PhaseTiming(TerminologyPhase.QUERY, 0.01),
                PhaseTiming(
                    TerminologyPhase.EXTERNAL_LLM_WAIT,
                    0.0,
                    (("status", "disabled"), ("requests", 0)),
                ),
                PhaseTiming(TerminologyPhase.CLEANUP, 0.001),
            ),
            peak_memory_bytes=1024,
            memory_measurement="psutil-rss-peak",
            rss_baseline_bytes=10_000,
            rss_peak_bytes=11_024,
            rss_recovered_bytes=10_000,
        )
        for index in range(1, FORMAL_REPETITIONS + 1)
    )

    payload = BenchmarkManifest(
        scenario="query",
        environment={},
        dataset={},
        cache_preparation={},
        runs=runs,
    ).to_dict()

    assert payload["summary"]["external-llm-wait"]["raw_samples"] == [0.0] * FORMAL_REPETITIONS
    assert payload["runs"][0]["timings"][1]["details"] == {"status": "disabled", "requests": 0}


def test_all_formal_contract_inputs_are_required_before_gate_eligibility() -> None:
    runs = tuple(
        BenchmarkRun(
            iteration=index,
            timings=(
                PhaseTiming(TerminologyPhase.QUERY, 0.01),
                PhaseTiming(TerminologyPhase.CLEANUP, 0.001),
            ),
            peak_memory_bytes=1024,
            memory_measurement="psutil-rss-peak",
            rss_baseline_bytes=10_000,
            rss_peak_bytes=11_024,
            rss_recovered_bytes=10_000,
        )
        for index in range(1, FORMAL_REPETITIONS + 1)
    )
    payload = BenchmarkManifest(
        scenario="query",
        environment={
            "reference_device_id": "win11-ref-01",
            "cpu_model": "contract CPU",
            "physical_cores": 4,
            "windows": {"build": 22_000},
            "memory_bytes": 16 * 1024**3,
            "disk_type": "NVMe",
            "python": "3.12",
            "transbridge_build": "contract-build",
            "measurement_tools": {"clock": "time.perf_counter"},
            "reference_requirements_met": True,
        },
        dataset={
            "spec": {"name": "regular"},
            "expected_counts": {
                "sources": 50,
                "evidence": 250_000,
                "candidates": 55_000,
                "terminology": 50_000,
                "conflict_groups": 5_000,
                "versions": 10,
            },
            "canonical_digest": "a" * 64,
            "adapter_templates": [
                {
                    "format_id": f"format-{index}",
                    "adapter_id": f"adapter-{index}",
                    "adapter_version": "1",
                    "sha256": f"{index:x}" * 64,
                }
                for index in range(5)
            ],
        },
        cache_preparation={},
        runs=runs,
        workload=FORMAL_WORKLOAD,
        evidence_profile=BenchmarkEvidenceProfile.FORMAL_GATE,
        completed_semantics=SCENARIO_REQUIRED_SEMANTICS["query"],
    ).to_dict()

    assert payload["evidence"]["gate_eligible"] is True
    assert payload["evidence"]["gate_ineligibility_reasons"] == []


def test_measure_phase_records_failed_work_without_swallowing_exception() -> None:
    timings: list[PhaseTiming] = []

    with pytest.raises(RuntimeError, match="broken workload"):
        with measure_phase(TerminologyPhase.REDUCE, timings, details=(("source_id", "source-1"),)):
            raise RuntimeError("broken workload")

    assert len(timings) == 1
    assert timings[0].phase is TerminologyPhase.REDUCE
    assert dict(timings[0].details) == {"source_id": "source-1"}
    assert timings[0].duration_seconds >= 0
