"""Isolated sampling utilities for the release S03 performance gates.

This module provides the measurement primitives the gates are built on:

* ``percentile`` — self-implemented percentile (linear interpolation), so no
  new dependency is required and the definition is pinned and auditable.
* ``sample_time`` — wall-clock / latency sampler built on ``time.perf_counter``
  (a monotonic clock), which is deliberately independent of the application's
  business clock (``datetime``-based). This separation is what keeps a drift in
  the business clock from corrupting latency samples.
* ``current_rss_bytes`` / ``measure_rss_growth`` — resident set size in the
  current process via ``psutil`` when available; falls back to a
  ``tracemalloc``-based proxy and clearly labels it as an approximation, never
  as exact RSS.
* ``run_isolated`` — a subprocess helper for cross-process measurements (e.g.
  peak RSS of a parse that we do not want to pollute with the pytest process).
"""

from __future__ import annotations

from collections.abc import Callable
import gc
import json
import os
import statistics
import subprocess
import sys
import time
from typing import Any

try:  # psutil is an optional measurement dependency, not a runtime requirement.
    import psutil
except Exception:  # pragma: no cover - fallback path
    psutil = None  # type: ignore[assignment]

try:
    import tracemalloc
except Exception:  # pragma: no cover
    tracemalloc = None  # type: ignore[assignment]


def percentile(samples: list[float], p: float) -> float:
    """Linear-interpolation percentile (0 < p <= 100) of a sorted sample list.

    Matches the statistics screen the budgets were confirmed against. An empty
    sample list raises ``ValueError`` so a gate can never silently pass on no
    data.
    """
    if not samples:
        raise ValueError("percentile requires at least one sample")
    if not 0 < p <= 100:
        raise ValueError("percentile must satisfy 0 < p <= 100")
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (p / 100.0)
    lower = int(rank)
    upper = lower + 1 if lower + 1 < len(ordered) else lower
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def p95(samples: list[float]) -> float:
    return percentile(samples, 95.0)


def p50(samples: list[float]) -> float:
    return percentile(samples, 50.0)


def sample_time(
    runner: Callable[[], Any],
    *,
    warmup: int = 1,
    repetitions: int = 5,
) -> list[float]:
    """Collect ``repetitions`` latency samples in seconds using perf_counter.

    ``runner`` is invoked once per sample; exceptions propagate so a broken
    workload is a hard failure rather than a misleading low/high sample.
    """
    for _ in range(warmup):
        runner()
    samples: list[float] = []
    for _ in range(repetitions):
        start = time.perf_counter()
        runner()
        samples.append(time.perf_counter() - start)
    return samples


def force_gc() -> None:
    """Run a full GC pass so RSS samples are taken on a stable heap."""
    gc.collect()
    try:
        gc.collect()
    except Exception:  # pragma: no cover - defensive on unusual interpreters
        pass


def current_rss_bytes() -> int:
    """Resident set size (bytes) of the current process.

    Uses ``psutil`` (exact RSS on supported platforms). Raises ``RuntimeError``
    on platforms where RSS is not measurable so a gate must not pass on a
    bogus/zero reading; callers then fall back to a tracemalloc proxy.
    """
    if psutil is None:
        raise RuntimeError("psutil unavailable; RSS is not measureable in this process")
    try:
        return int(psutil.Process().memory_info().rss)
    except Exception as exc:  # pragma: no cover - psutil platform edge
        raise RuntimeError(f"RSS measurement failed: {exc}") from exc


def _tracemalloc_current() -> float:
    """Return current allocated-byte count via tracemalloc (approximation)."""
    if tracemalloc is None:
        raise RuntimeError("neither psutil nor tracemalloc is available; cannot measure memory")
    if not tracemalloc.is_tracing():
        tracemalloc.start()
    current, _peak = tracemalloc.get_traced_memory()
    return float(current)


def measure_rss_growth(
    round_fn: Callable[[int], Any],
    rounds: int = 500,
    *,
    samples_every: int = 50,
) -> dict[str, Any]:
    """Measure resident-set growth over ``rounds`` workload iterations.

    ``round_fn(i)`` grows in-memory session state for the i-th round. RSS is
    sampled every ``samples_every`` rounds on a GC-stabilised heap.

    Returns absolute samples plus the growth ratio between the first and last
    usable sample. When exact RSS (psutil) is unavailable, values are derived
    from the tracemalloc proxy and the result is explicitly labelled as an
    approximation rather than exact RSS.
    """
    use_exact = True
    try:
        current_rss_bytes()
    except RuntimeError:
        use_exact = False

    samples: list[float] = []
    indices: list[int] = []
    for i in range(rounds):
        round_fn(i)
        if i % samples_every == 0 or i == rounds - 1:
            force_gc()
            if use_exact:
                value = float(current_rss_bytes())
            else:
                value = _tracemalloc_current()
            samples.append(value)
            indices.append(i)

    if len(samples) < 2:
        raise RuntimeError("not enough RSS samples to compute growth")
    base = samples[0]
    if base <= 0:
        raise RuntimeError("baseline RSS is zero; cannot compute growth")
    peak = max(samples)
    growth_ratio = (peak - base) / base
    return {
        "indices": indices,
        "samples_bytes": samples,
        "base_bytes": base,
        "peak_bytes": peak,
        "growth_ratio": growth_ratio,
        "exact": use_exact,
        "proxy": "psutil" if use_exact else "tracemalloc-approximation",
    }


def run_isolated(runner_argv: list[str], *, timeout: int = 120) -> dict[str, Any]:
    """Run an isolated subprocess and parse its JSON result record.

    ``runner_argv[0]`` is a Python expression body run via ``sys.executable -c
    - <args>`` (or ``-c <body>``); the child must print a single line of JSON
    to stdout. Used where a peak-RSS / timing measurement must not be polluted
    by the parent process (e.g. the medium-parse RSS boundary check).
    """
    body = runner_argv[0]
    extra = runner_argv[1:]
    repo_root = str(REPO_ROOT)
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = repo_root + (os.pathsep + existing if existing else "")
    completed = subprocess.run(
        [sys.executable, "-c", body, *extra],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        cwd=repo_root,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"isolated runner failed ({completed.returncode}): {completed.stderr[-800:]}")
    try:
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"isolated runner did not emit JSON: {exc}") from exc


def summarize(samples: list[float], *, unit: str = "s") -> dict[str, Any]:
    """Compact summary of a latency sample set used in reports/gates."""
    return {
        "count": len(samples),
        "p50": p50(samples),
        "p95": p95(samples),
        "mean": statistics.mean(samples),
        "min": min(samples),
        "max": max(samples),
        "unit": unit,
    }


# Resolve the repository root once at import (must not hard-code paths).
from pathlib import Path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
