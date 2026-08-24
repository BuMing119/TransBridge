from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

from . import benchmark_cases as cases


def test_ui_modularization_cases_are_versioned_and_valid() -> None:
    for case_id in (
        "ui-modularization-window-open",
        "ui-modularization-interaction",
        "ui-modularization-lifecycle",
    ):
        case = cases.get_case(case_id)
        assert case.kind == "ui-modularization"


def test_ui_modularization_relative_budgets_match_nfr_1_5() -> None:
    thresholds = cases.THRESHOLDS_V1
    assert thresholds.ui_modularization_max_regression_ratio == 0.05
    assert thresholds.ui_modularization_absolute_regression_s == 0.010
    assert thresholds.ui_lifecycle_iterations == 100


def _load_compare_module():
    path = Path("scripts/compare_ui_modularization_benchmark.py")
    spec = importlib.util.spec_from_file_location("fr25_benchmark_compare", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_relative_budget_comparator_rejects_real_regression() -> None:
    compare = _load_compare_module()
    baseline = {
        "cold_start": {"p95_s": 0.200},
        "window_open": {"p95_s": 0.100},
        "interaction": {"p95_s": 0.400},
        "heartbeat_max_s": 0.050,
        "lifecycle": {"iterations": 100, "live_python_wrappers": 0, "rss_delta_bytes": 1_000_000},
    }
    candidate = {
        "cold_start": {"p95_s": 0.250},
        "window_open": {"p95_s": 0.111},
        "interaction": {"p95_s": 0.500},
        "heartbeat_max_s": 0.250,
        "lifecycle": {"iterations": 99, "live_python_wrappers": 1, "rss_delta_bytes": 9_000_000},
    }

    failures = compare.evaluate(baseline, candidate)

    assert any("cold_start" in failure for failure in failures)
    assert any("window_open" in failure for failure in failures)
    assert any("interaction" in failure for failure in failures)
    assert any("heartbeat" in failure for failure in failures)
    assert any("lifecycle sample" in failure for failure in failures)
    assert any("wrappers" in failure for failure in failures)
    assert any("RSS" in failure for failure in failures)


def test_current_worktree_executes_window_render_and_100_cycle_gate() -> None:
    command = [
        sys.executable,
        "scripts/benchmark_ui_modularization.py",
        "--source-root",
        ".",
        "--entries",
        "1200",
        "--repetitions",
        "3",
        "--lifecycle-iterations",
        "100",
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    result = json.loads(completed.stdout)

    assert result["interaction"]["p95_s"] > 0
    assert result["window_open"]["p95_s"] > 0
    assert result["heartbeat_max_s"] <= 0.200
    assert result["lifecycle"]["iterations"] == 100
    assert result["lifecycle"]["live_python_wrappers"] == 0
