"""Run the reproducible FR25 Step2 window/render/lifecycle benchmark.

The benchmark accepts an extracted source tree so the same runner can compare
the S01 Git baseline with the current worktree on one machine.  It emits JSON
only; threshold evaluation belongs to the caller/reporting gate.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
from pathlib import Path
import statistics
import sys
import time
import weakref


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * percentile + 0.999999) - 1))
    return ordered[index]


def _rss_bytes() -> int:
    try:
        import psutil

        return psutil.Process().memory_info().rss
    except (ImportError, OSError):
        return 0


def _measure(repetitions: int, operation) -> dict[str, float]:
    samples: list[float] = []
    for _ in range(repetitions):
        started = time.perf_counter()
        operation()
        samples.append(time.perf_counter() - started)
    return {
        "p50_s": statistics.median(samples),
        "p95_s": _percentile(samples, 0.95),
        "max_s": max(samples),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("--entries", type=int, default=1_200)
    parser.add_argument("--repetitions", type=int, default=20)
    parser.add_argument("--lifecycle-iterations", type=int, default=100)
    args = parser.parse_args(argv)

    source_root = args.source_root.resolve()
    sys.path.insert(0, str(source_root / "src"))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    from transbridge.converter.translation_entry import TranslationEntry
    from transbridge.converter.translation_entry_collection import TranslationEntryCollection
    from transbridge.ui import context as context_module
    from transbridge.ui.workbench.step2 import Step2PreviewWidget

    class _Config:
        token = ""

    context_module.ParatranzConfig.create_or_load = classmethod(lambda _cls: _Config())
    app = QApplication.instance() or QApplication([])

    def make_entries() -> TranslationEntryCollection:
        return TranslationEntryCollection(
            TranslationEntry(str(index), f"key-{index:05d}", f"Original {index}", "", 0, "NPC_:FULL")
            for index in range(args.entries)
        )

    warmups = 2
    for _ in range(warmups):
        widget = Step2PreviewWidget(context_module.AppContext())
        widget.close()
        widget.deleteLater()
        app.processEvents()

    def open_window() -> None:
        widget = Step2PreviewWidget(context_module.AppContext())
        widget.resize(1_200, 800)
        widget.show()
        app.processEvents()
        widget.close()
        widget.deleteLater()
        app.processEvents()

    window_open = _measure(args.repetitions, open_window)

    widget = Step2PreviewWidget(context_module.AppContext())
    widget.resize(1_200, 800)
    widget.show()
    collection = make_entries()
    heartbeat_gaps: list[float] = []

    def render_collection() -> None:
        ticks = [time.perf_counter()]
        heartbeat = QTimer()
        heartbeat.setInterval(10)
        heartbeat.timeout.connect(lambda: ticks.append(time.perf_counter()))
        heartbeat.start()
        widget.refresh(collection)
        deadline = time.monotonic() + 10.0
        while widget._table.rowCount() < args.entries and time.monotonic() < deadline:
            app.processEvents()
        ticks.append(time.perf_counter())
        heartbeat.stop()
        heartbeat_gaps.append(max(right - left for left, right in zip(ticks, ticks[1:])))
        if widget._table.rowCount() != args.entries:
            raise RuntimeError(f"render timed out at {widget._table.rowCount()}/{args.entries} rows")

    interaction = _measure(args.repetitions, render_collection)
    widget.close()
    widget.deleteLater()
    app.processEvents()

    rss_before = _rss_bytes()
    references: list[weakref.ReferenceType] = []
    lifecycle_max_s = 0.0
    for _ in range(args.lifecycle_iterations):
        started = time.perf_counter()
        candidate = Step2PreviewWidget(context_module.AppContext())
        references.append(weakref.ref(candidate))
        candidate.close()
        candidate.deleteLater()
        app.processEvents()
        del candidate
        lifecycle_max_s = max(lifecycle_max_s, time.perf_counter() - started)
    gc.collect()
    app.processEvents()
    gc.collect()
    rss_after = _rss_bytes()

    result = {
        "source_root": str(source_root),
        "entries": args.entries,
        "repetitions": args.repetitions,
        "window_open": window_open,
        "interaction": interaction,
        "heartbeat_max_s": max(heartbeat_gaps),
        "lifecycle": {
            "iterations": args.lifecycle_iterations,
            "max_cycle_s": lifecycle_max_s,
            "live_python_wrappers": sum(reference() is not None for reference in references),
            "rss_before_bytes": rss_before,
            "rss_after_bytes": rss_after,
            "rss_delta_bytes": rss_after - rss_before if rss_before and rss_after else 0,
        },
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
