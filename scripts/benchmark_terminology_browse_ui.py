"""Measure real SQLite cold effective-term paging through the Qt query model.

Synthetic data only. Fixture creation, fresh repository/adapter opening, result
checks and disposal are outside timing. Cold means fresh application adapters,
not an OS cache flush. Completion is first-page delivery into the Qt table model,
including its row update, but not subsequent table painting. No network or LLM.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
from pathlib import Path
import platform
import statistics
import sys
from threading import get_ident
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6 import sip  # noqa: E402
from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, Qt, QThreadPool, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from scripts.benchmark_terminology_storage import PROJECT, VARIANT, create_fixture, open_browse  # noqa: E402
from transbridge.application.terminology.browse_queries import TermBrowseQuery  # noqa: E402
from transbridge.ui.tools.terminology.paged_models import KeysetPagedTableModel, PagedColumn  # noqa: E402


def _check(model, browsing, profiles, count):
    expected_count = min(100, count)
    assert model.rowCount() == expected_count and not model.is_loading and model.last_error is None
    rows = tuple(model.data(model.index(index, 0), Qt.ItemDataRole.UserRole) for index in range(expected_count))
    assert [item.term_id for item in rows] == [f"term-{index:06d}" for index in range(expected_count)]
    assert [item.translation for item in rows] == [f"方案 术语 {index:06d}" for index in range(expected_count)]
    assert all(item.project_id == PROJECT and item.variant_id == VARIANT and item.is_effective for item in rows)
    assert model.pagination.total == count and model.pagination.last_row == expected_count
    assert model.pagination.first_row == 1 and model.pagination.can_next == (count > expected_count)
    description = browsing.describe(TermBrowseQuery(PROJECT, VARIANT))
    selected = profiles.selected_revision(PROJECT, VARIANT)
    assert selected is not None and description.profile_id == selected.profile_id
    assert description.profile_name == "Benchmark" and description.profile_revision == selected.revision == 1
    assert description.base_version_id == "version-1" and description.snapshot_digest == model.snapshot_digest


def sample(app, path, count):
    repository, profiles, _base, browsing = open_browse(path)
    owner, pool = QWidget(), QThreadPool()
    pool.setMaxThreadCount(1)
    completed, errors, worker_threads, ticks = [], [], [], []
    ui_thread = get_ident()

    def load(query, request, token):
        worker_threads.append(get_ident())
        return browsing.list_effective_terms(query, request, cancellation=token)

    model = KeysetPagedTableModel(
        browsing.list_effective_terms,
        (PagedColumn("term_id", "ID", lambda item: item.term_id),),
        owner,
        page_size=100,
        manual_paging=True,
        thread_pool=pool,
        read_loader=load,
    )
    model.page_loaded.connect(lambda size: completed.append((time.perf_counter(), size, get_ident())))
    model.query_failed.connect(errors.append)
    timer = QTimer(owner)
    timer.setTimerType(Qt.TimerType.PreciseTimer)
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
    try:
        owner.show()
        app.processEvents()
        gc.collect()
        app.processEvents()
        timer.start()
        started = time.perf_counter()
        model.set_query(TermBrowseQuery(PROJECT, VARIANT))
        submitted = time.perf_counter()
        while not completed and not errors:
            if time.perf_counter() - started > 120:
                raise TimeoutError("cold first page was not delivered within 120 seconds")
            app.processEvents()
            time.sleep(0.001)
        timer.stop()
        if errors:
            raise RuntimeError(f"effective-term paging failed: {errors[-1]}")
        finished, delivered_count, delivered_thread = completed[0]
        assert len(completed) == 1 and delivered_count == min(100, count)
        assert worker_threads and ui_thread not in worker_threads and delivered_thread == ui_thread
        _check(model, browsing, profiles, count)
        boundaries = [started, *(tick for tick in ticks if started < tick < finished), finished]
        return {
            "submission_ms": (submitted - started) * 1000,
            "completion_ms": (finished - started) * 1000,
            "max_event_loop_gap_ms": max(b - a for a, b in zip(boundaries, boundaries[1:])) * 1000,
            "heartbeat_ticks": len(boundaries) - 2,
        }
    finally:
        timer.stop()
        model.close()
        stopped = pool.waitForDone(60_000)
        if stopped:
            repository.close()
        owner.close()
        sip.delete(owner)
        if not stopped:
            raise TimeoutError("paging worker did not stop; database retained until process exit")


def measure(app, path, count, repeats):
    create_fixture(path, count)
    samples = [sample(app, path, count) for _ in range(repeats)]
    return {
        "count": count,
        "samples": samples,
        "metrics_ms": {
            key: {
                "median": statistics.median(row[key] for row in samples),
                "min": min(row[key] for row in samples),
                "max": max(row[key] for row in samples),
            }
            for key in ("submission_ms", "completion_ms", "max_event_loop_gap_ms")
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sizes", nargs="+", type=int, default=[10000, 50000])
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if args.repeats < 5 or any(size < 1 for size in args.sizes):
        parser.error("at least five repetitions and positive sizes are required")
    if sys.flags.optimize:
        parser.error("run without -O: result assertions are required")
    if any(path.exists() or path.is_symlink() for path in (args.workspace, args.output)):
        parser.error("workspace and output must be new paths")
    args.workspace, args.output = args.workspace.resolve(), args.output.resolve()
    if args.workspace.is_relative_to(args.output):
        parser.error("output cannot be the workspace or its parent")
    args.workspace.mkdir(parents=True, exist_ok=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    report = {
        "scenario": "transbridge.synthetic-terminology-cold-browse-ui.v1",
        "status": "running",
        "python": sys.version,
        "os": platform.platform(),
        "qt": QT_VERSION_STR,
        "pyqt": PYQT_VERSION_STR,
        "platform": app.platformName(),
        "command": sys.argv,
        "synthetic": True,
        "scope": "fresh SQLite repository/adapters/pool per sample; 100 effective rows with selected profile",
        "cold": "fresh application indexes/caches, not OS disk cache; setup/opening excluded",
        "completion": "set_query to page_loaded after Qt model accepts rows; table painting excluded",
        "heartbeat": "10ms precise QTimer gaps include period, scheduling, polling and GIL contention; not pure CPU time",
        "results": [],
    }
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    for size in args.sizes:
        value = measure(app, args.workspace / f"data-{size}", size, args.repeats)
        report["results"].append(value)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(value, ensure_ascii=False), flush=True)
    report["status"] = "complete"
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
