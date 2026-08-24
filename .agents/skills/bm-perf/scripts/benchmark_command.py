#!/usr/bin/env python3
"""Run a local command repeatedly and report wall-clock timing as JSON."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import statistics
import subprocess
import time


@dataclass
class RunResult:
    run: int
    elapsed_seconds: float
    return_code: int


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=5, help="Number of timed runs (default: 5)")
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command to run; place it after '--', for example: -- python -m pytest tests/test_x.py",
    )
    args = parser.parse_args()
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]

    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if not args.command:
        parser.error("provide a command after '--'")

    results: list[RunResult] = []
    for run in range(1, args.runs + 1):
        started = time.perf_counter()
        completed = subprocess.run(args.command, check=False)
        results.append(
            RunResult(
                run=run,
                elapsed_seconds=time.perf_counter() - started,
                return_code=completed.returncode,
            )
        )
        if completed.returncode:
            break

    elapsed = [result.elapsed_seconds for result in results]
    print(
        json.dumps(
            {
                "command": args.command,
                "requested_runs": args.runs,
                "completed_runs": len(results),
                "median_seconds": statistics.median(elapsed),
                "min_seconds": min(elapsed),
                "max_seconds": max(elapsed),
                "runs": [asdict(result) for result in results],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if all(result.return_code == 0 for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
