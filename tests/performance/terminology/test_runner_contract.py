from __future__ import annotations

import pytest

from scripts.benchmark_project_terminology import _cache_preparation, _diagnostic_manifest

from .measure import FORMAL_REPETITIONS, SCENARIOS, BenchmarkRun, PhaseTiming, TerminologyPhase


def _complete_phase_runs() -> tuple[BenchmarkRun, ...]:
    return tuple(
        BenchmarkRun(
            iteration=index,
            timings=tuple(PhaseTiming(phase, 0.001) for phase in TerminologyPhase),
            peak_memory_bytes=1024,
            memory_measurement="contract-fixture",
        )
        for index in range(1, FORMAL_REPETITIONS + 1)
    )


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_diagnostic_runner_is_ineligible_even_on_formal_scale_and_reference_host(scenario: str) -> None:
    manifest = _diagnostic_manifest(
        scenario,
        {"reference_requirements_met": True},
        {"spec": {"name": "regular"}},
        _cache_preparation(scenario),
        _complete_phase_runs(),
    ).to_dict()

    evidence = manifest["evidence"]
    reasons = evidence["gate_ineligibility_reasons"]
    assert evidence["gate_eligible"] is False
    assert evidence["profile"] == "diagnostic-smoke-v1"
    assert "evidence-profile-not-formal:diagnostic-smoke-v1" in reasons
    assert "workload-not-formal:project-terminology-diagnostic-smoke-v2" in reasons
    assert evidence["scenario_contract"]["missing_required_semantics"]
    assert any(reason.startswith("declared-limitation:") for reason in reasons)


@pytest.mark.parametrize("scenario", ("full-warm", "repeat"))
def test_diagnostic_runner_does_not_claim_warm_or_repeat_cache_retention(scenario: str) -> None:
    preparation = _cache_preparation(scenario)

    assert preparation["project_result_cache"] == "fresh-repository-per-iteration"
    assert preparation["requested_warm_or_repeat_cache"] == "not-retained-fresh-repository-per-iteration"
    assert preparation["formal_scenario_semantics"] == "not-implemented-diagnostic-smoke"


def test_diagnostic_changed_scenario_declares_full_reduction_limitations() -> None:
    preparation = _cache_preparation("changed-10pct")

    assert preparation["change_fraction_max"] == 0.10
    assert preparation["incremental_reuse"] == "not-executed-full-reduction-only"
    assert preparation["full_rebuild_digest_comparison"] == "not-executed"
