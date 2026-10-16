"""Measure real SQLite draft writes through the Qt serial submission queue.

Uses synthetic fixtures only. Setup, post-commit verification and disposal are
outside timing. The completion measurement ends at the UI saved callback and
does not include the workbench's subsequent asset refresh. No network or LLM.
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
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, QThreadPool, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from scripts.benchmark_terminology_storage import commands_for, create_fixture, open_browse  # noqa: E402
from transbridge.ui.tools.terminology.draft_write_queue import DraftWriteQueue  # noqa: E402


def measure(app, path, count, repeats):
    create_fixture(path, count)
    repository, _profiles, _base, _browsing = open_browse(path)
    commands, context = commands_for(repository)
    draft = repository.active_draft("project-1", "variant-1")
    identity, initial_revision = draft.decisions[-1].term_id, draft.ref.revision
    owner = QWidget()
    owner.show()
    app.processEvents()
    completed, errors = [], []
    queue = DraftWriteQueue(
        owner,
        on_started=lambda: None,
        on_saved=lambda value: completed.append((time.perf_counter(), value)),
        on_error=errors.append,
    )
    samples = []
    try:
        for iteration in range(repeats):
            gc.collect()
            app.processEvents()
            ticks = [time.perf_counter()]
            timer = QTimer()
            timer.setInterval(10)
            timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
            timer.start()
            started = time.perf_counter()
            queue.submit(
                lambda iteration=iteration: commands.apply_decision(
                    "change_translation", context, term_id=identity, translation=f"编辑后的译名 {iteration}"
                )
            )
            submitted = time.perf_counter()
            try:
                while len(completed) <= iteration and not errors:
                    if time.perf_counter() - started > 60:
                        raise TimeoutError("real SQLite draft submission did not finish")
                    app.processEvents()
                    time.sleep(0.001)
            finally:
                ticks.append(time.perf_counter())
                timer.stop()
            if errors:
                raise errors[-1]
            finished, result = completed[-1]
            assert result.ref.revision == initial_revision + iteration + 1
            stored = repository.active_draft("project-1", "variant-1")
            assert stored.ref == result.ref and stored.decisions[-1].translation == f"编辑后的译名 {iteration}"
            samples.append({
                "submit_ms": (submitted - started) * 1000,
                "completion_ms": (finished - started) * 1000,
                "max_event_loop_gap_ms": max(b - a for a, b in zip(ticks, ticks[1:])) * 1000,
            })
    finally:
        queue.close()
        owner.close()
        if not QThreadPool.globalInstance().waitForDone(60_000):
            raise TimeoutError("draft worker did not stop")
        repository.close()
        owner.deleteLater()
        app.processEvents()
    return {
        "count": count,
        "samples": samples,
        "metrics": {
            key: {
                "median": statistics.median(row[key] for row in samples),
                "min": min(row[key] for row in samples),
                "max": max(row[key] for row in samples),
            }
            for key in samples[0]
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sizes", nargs="+", type=int, default=[1000, 10000])
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if args.repeats < 5 or any(size < 1 for size in args.sizes):
        parser.error("at least five repetitions and positive sizes are required")
    if sys.flags.optimize:
        parser.error("run without -O: result assertions are required")
    if any(path.exists() or path.is_symlink() for path in (args.workspace, args.output)):
        parser.error("workspace and output must be new paths")
    if args.workspace.resolve().is_relative_to(args.output.resolve()):
        parser.error("output cannot be the workspace or its parent")
    args.workspace, args.output = args.workspace.resolve(), args.output.resolve()
    args.workspace.mkdir(parents=True, exist_ok=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    report = {
        "python": sys.version,
        "os": platform.platform(),
        "qt": QT_VERSION_STR,
        "pyqt": PYQT_VERSION_STR,
        "command": sys.argv,
        "synthetic": True,
        "platform": app.platformName(),
        "scope": "real SQLite command via DraftWriteQueue; UI saved callback; excludes post-save asset refresh",
        "heartbeat": "10ms QTimer gaps include timer period, scheduling and polling; not pure callback CPU time",
        "results": [],
    }
    for size in args.sizes:
        value = measure(app, args.workspace / f"data-{size}", size, args.repeats)
        report["results"].append(value)
        print(json.dumps(value, ensure_ascii=False), flush=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
