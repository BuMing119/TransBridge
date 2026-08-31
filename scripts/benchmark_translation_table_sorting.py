"""Measure offline table sorting: first batch, full render and event-loop gaps.

Example: uv run python scripts/benchmark_translation_table_sorting.py --entries 10000 --repetitions 5
Use --select-all to include preserving a large row selection. Output is JSON;
measurements are local evidence, not hardware-independent performance gates.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import time
from types import SimpleNamespace


def _summary(samples: list[float]) -> dict[str, float]:
    return {"median_ms": statistics.median(samples), "min_ms": min(samples), "max_ms": max(samples)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entries", type=int, default=10_000)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--select-all", action="store_true")
    args = parser.parse_args()
    if args.entries < 1 or args.repetitions < 1:
        parser.error("entries and repetitions must be positive")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, QTimer
    from PyQt6.QtWidgets import QApplication

    from transbridge.converter.translation_entry import TranslationEntry
    from transbridge.converter.translation_entry_collection import TranslationEntryCollection
    from transbridge.ui import context as context_module
    from transbridge.ui.workbench.step2 import Step2PreviewWidget
    from transbridge.ui.workbench.translation_table_columns import COL_ORIGINAL
    from transbridge.ui.workbench.translation_table_sorting import ordered_source_rows

    app = QApplication.instance() or QApplication([])
    entries = tuple(
        TranslationEntry(
            str(index),
            f"key-{index:06d}",
            f"Original {(index * 7919) % args.entries:06d} " + "text " * 40,
            f"Translation {index}" if index % 3 else "",
            index % 3,
            "NPC_:FULL",
        )
        for index in range(args.entries)
    )
    source_ids = tuple(entry.id for entry in entries)
    context_module.ParatranzConfig.create_or_load = classmethod(lambda _cls: SimpleNamespace(token=""))
    widget = Step2PreviewWidget(context_module.AppContext())
    widget.resize(1_200, 800)
    widget.show()
    table = widget._table

    def drain() -> None:
        deadline = time.monotonic() + 60
        while table.has_pending_batch and time.monotonic() < deadline:
            app.processEvents()
        if table.rowCount() != args.entries or table.has_pending_batch:
            raise RuntimeError(f"render timed out at {table.rowCount()}/{args.entries}")

    widget.refresh(TranslationEntryCollection(entries))
    session = table.render_session
    drain()
    if args.select_all:
        table.selectAll()
    key_ms, first_ms, full_ms, gap_ms = [], [], [], []
    try:
        for _ in range(args.repetitions):
            started = time.perf_counter()
            ordered_source_rows(entries, {}, COL_ORIGINAL)
            key_ms.append((time.perf_counter() - started) * 1_000)
            # Return to default between samples so every measured sort is ascending.
            ticks = [time.perf_counter()]
            heartbeat = QTimer()
            heartbeat.setInterval(10)
            heartbeat.timeout.connect(lambda: ticks.append(time.perf_counter()))
            heartbeat.start()
            started = time.perf_counter()
            table.horizontalHeader().sectionClicked.emit(COL_ORIGINAL)
            first_ms.append((time.perf_counter() - started) * 1_000)
            drain()
            ended = time.perf_counter()
            ticks.append(ended)
            heartbeat.stop()
            full_ms.append((ended - started) * 1_000)
            gap_ms.append(max(right - left for left, right in zip(ticks, ticks[1:])) * 1_000)
            assert table.render_session is session
            assert tuple(entry.id for entry in entries) == source_ids
            if args.select_all:
                assert len(table.selected_entry_ids()) == args.entries
            table.horizontalHeader().sectionClicked.emit(COL_ORIGINAL)
            table.horizontalHeader().sectionClicked.emit(COL_ORIGINAL)
            drain()
        print(
            json.dumps(
                {
                    "python": platform.python_version(),
                    "qt": QT_VERSION_STR,
                    "pyqt": PYQT_VERSION_STR,
                    "platform": platform.platform(),
                    "qt_platform": os.environ["QT_QPA_PLATFORM"],
                    "entries": args.entries,
                    "repetitions": args.repetitions,
                    "select_all": args.select_all,
                    "view": "Step2PreviewWidget",
                    "sort_indices": _summary(key_ms),
                    "first_batch": _summary(first_ms),
                    "full_render": _summary(full_ms),
                    "event_loop_max_gap": _summary(gap_ms),
                },
                sort_keys=True,
            )
        )
    finally:
        widget.close()


if __name__ == "__main__":
    main()
