"""Compare FR25 baseline/candidate benchmark output and enforce NFR 1.5."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time


def _run(root: Path, *, entries: int, repetitions: int, lifecycle: int) -> dict:
    runner = Path(__file__).with_name("benchmark_ui_modularization.py")
    command = [
        sys.executable,
        str(runner),
        "--source-root",
        str(root),
        "--entries",
        str(entries),
        "--repetitions",
        str(repetitions),
        "--lifecycle-iterations",
        str(lifecycle),
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * percentile + 0.999999) - 1))
    return ordered[index]


def _cold_import(root: Path, repetitions: int) -> dict[str, float]:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["PYTHONPATH"] = os.pathsep.join([str(root.resolve() / "src"), environment.get("PYTHONPATH", "")])
    command = [sys.executable, "-c", "import transbridge.ui.main_window"]
    samples: list[float] = []
    for index in range(repetitions + 2):
        started = time.perf_counter()
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            cwd=root,
            env=environment,
        )
        elapsed = time.perf_counter() - started
        if index >= 2:
            samples.append(elapsed)
    return {
        "p50_s": statistics.median(samples),
        "p95_s": _percentile(samples, 0.95),
        "max_s": max(samples),
    }


def allowed_time_delta(baseline_s: float) -> float:
    return max(baseline_s * 0.05, 0.010)


def evaluate(baseline: dict, candidate: dict) -> list[str]:
    failures: list[str] = []
    for metric in ("cold_start", "window_open", "interaction"):
        before = float(baseline[metric]["p95_s"])
        after = float(candidate[metric]["p95_s"])
        if after - before > allowed_time_delta(before):
            failures.append(
                f"{metric}.p95 regressed by {after - before:.6f}s; allowed {allowed_time_delta(before):.6f}s"
            )
    if float(candidate["heartbeat_max_s"]) > 0.200:
        failures.append(f"heartbeat {candidate['heartbeat_max_s']:.6f}s exceeds 0.200s")
    lifecycle = candidate["lifecycle"]
    if int(lifecycle["iterations"]) < 100:
        failures.append("candidate lifecycle sample is below 100 iterations")
    if int(lifecycle["live_python_wrappers"]) != 0:
        failures.append(f"{lifecycle['live_python_wrappers']} UI wrappers survived lifecycle gate")
    before_rss = max(0, int(baseline["lifecycle"]["rss_delta_bytes"]))
    after_rss = max(0, int(lifecycle["rss_delta_bytes"]))
    rss_allowance = max(int(before_rss * 0.05), 4 * 1024 * 1024)
    if after_rss - before_rss > rss_allowance:
        failures.append(f"lifecycle RSS delta regressed by {after_rss - before_rss} bytes; allowed {rss_allowance}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, default=Path.cwd())
    parser.add_argument("--entries", type=int, default=10_000)
    parser.add_argument("--repetitions", type=int, default=20)
    parser.add_argument("--lifecycle-iterations", type=int, default=100)
    args = parser.parse_args(argv)

    baseline = _run(
        args.baseline_root,
        entries=args.entries,
        repetitions=args.repetitions,
        lifecycle=args.lifecycle_iterations,
    )
    candidate = _run(
        args.candidate_root,
        entries=args.entries,
        repetitions=args.repetitions,
        lifecycle=args.lifecycle_iterations,
    )
    baseline["cold_start"] = _cold_import(args.baseline_root, args.repetitions)
    candidate["cold_start"] = _cold_import(args.candidate_root, args.repetitions)
    failures = evaluate(baseline, candidate)
    print(json.dumps({"baseline": baseline, "candidate": candidate, "failures": failures}, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
