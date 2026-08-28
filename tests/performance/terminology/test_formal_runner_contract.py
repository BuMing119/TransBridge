from __future__ import annotations

from pathlib import Path

import pytest

from tests.performance.terminology.dataset import generate_terminology_dataset
from tests.performance.terminology.formal_runner import FormalBenchmarkExecutor

from .measure import SCENARIO_REQUIRED_PHASES, SCENARIO_REQUIRED_SEMANTICS, SCENARIOS


@pytest.mark.slow
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_every_formal_scenario_executes_its_production_contract_on_smoke_corpus(
    tmp_path: Path,
    scenario: str,
) -> None:
    dataset = generate_terminology_dataset(tmp_path / "dataset")
    executor = FormalBenchmarkExecutor(dataset, tmp_path / "workload")
    try:
        preparation = executor.prepare(scenario)
        run = executor.run(scenario, 1)
    finally:
        executor.close()

    observed = {timing.phase for timing in run.timings}
    assert SCENARIO_REQUIRED_PHASES[scenario] <= observed
    assert preparation["production_adapter_preflight"]["result"] == "all-five-fixtures-parsed"
    assert preparation["production_adapter_preflight"]["bulk_logical_evidence"] == (
        "deterministic-ndjson-contract-corpus"
    )
    assert run.rss_baseline_bytes is not None
    assert run.rss_peak_bytes is not None
    assert run.rss_recovered_bytes is not None
    assert run.peak_memory_bytes == max(0, run.rss_peak_bytes - run.rss_baseline_bytes)


def test_history_is_a_standalone_formal_scenario() -> None:
    assert "history" in SCENARIOS
    assert "persisted-version-history-first-page" in SCENARIO_REQUIRED_SEMANTICS["history"]
