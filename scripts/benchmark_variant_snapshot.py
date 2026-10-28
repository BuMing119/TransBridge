"""Compare repeated unchanged Variant reads against a Git version, without user data.

The baseline module is loaded in memory from git show; no checkout is changed.
This measures snapshot reads only, not end-to-end application responsiveness.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
from time import perf_counter
from types import ModuleType

from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.io.stage_policy import Stage
from transbridge.persistence.v2 import variant
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef


def measure(module, entries: int, reads: int, repetitions: int):
    namespace = SourceNamespace("source:benchmark")
    fingerprint = module.SourceFingerprint(namespace, "a" * 64)
    states = tuple(
        module.VariantEntryState(EntryKey(namespace, str(index)), "translation", Stage.TRANSLATED)
        for index in range(entries)
    )
    snapshot = module.VariantSnapshot(
        VariantRef(VariantId("benchmark"), ProjectId("benchmark")), (fingerprint,), states
    )
    aggregate = module.VariantAggregate(snapshot)
    times, rebuilt = [], []
    for _ in range(repetitions):
        previous = aggregate.snapshot()
        constructions = 0
        started = perf_counter()
        for _ in range(reads):
            current = aggregate.snapshot()
            constructions += current is not previous
            previous = current
        times.append(perf_counter() - started)
        rebuilt.append(constructions)
    return {
        "seconds": times,
        "median_seconds": statistics.median(times),
        "range_seconds": [min(times), max(times)],
        "new_snapshots_per_sample": rebuilt,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="HEAD")
    parser.add_argument("--entries", type=int, default=10_000)
    parser.add_argument("--reads", type=int, default=100)
    parser.add_argument("--repetitions", type=int, default=5)
    args = parser.parse_args()
    if min(args.entries, args.reads, args.repetitions) < 1:
        parser.error("sample sizes must be positive")
    baseline_revision = subprocess.check_output(["git", "rev-parse", "--verify", args.baseline], text=True).strip()
    source = subprocess.check_output([
        "git",
        "show",
        f"{baseline_revision}:src/transbridge/persistence/v2/variant.py",
    ]).decode("utf-8")
    name = "transbridge.persistence.v2._snapshot_benchmark_baseline"
    baseline = ModuleType(name)
    baseline.__package__ = "transbridge.persistence.v2"
    sys.modules[name] = baseline
    exec(compile(source, f"git:{baseline_revision}:variant.py", "exec"), baseline.__dict__)
    print(
        json.dumps(
            {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "baseline_revision": baseline_revision,
                "entries": args.entries,
                "reads": args.reads,
                "baseline": measure(baseline, args.entries, args.reads, args.repetitions),
                "working_tree": measure(variant, args.entries, args.reads, args.repetitions),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
