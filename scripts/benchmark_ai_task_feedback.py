"""Measure real AI-worker feedback delivery to a synthetic Qt task window.

Alternate --source between an isolated baseline src tree and the current src.
Two sources contain --entries synthetic entries in total and produce --events
progress/log pairs in total. The actual worker, source dispatcher, signal binding,
progress presenter, and log widget run normally. API calls, commits, snapshots,
task records, and the external task-runtime adapter are deliberately excluded.
Window construction and feedback delivery are reported separately.
With --gui-stall-ms, Qt delivery is withheld until the synthetic producer exits;
the reported recovery time excludes that deliberate stall and producer work.
"""

from __future__ import annotations

import argparse
from collections import Counter
import importlib
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import threading
from time import perf_counter, sleep
from types import SimpleNamespace
from unittest.mock import patch


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1] / "src")
    parser.add_argument("--entries", type=_positive_int, default=20_000)
    parser.add_argument("--events", type=_positive_int, default=1000)
    parser.add_argument("--repetitions", type=_positive_int, default=5)
    parser.add_argument("--gui-stall-ms", type=int, default=0)
    parser.add_argument("--waves", type=_positive_int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if min(args.entries, args.events) < 2:
        parser.error("--entries and --events must each allow at least one item per source")
    if args.gui_stall_ms < 0:
        parser.error("--gui-stall-ms must not be negative")
    return args


def _load_runtime(source: Path) -> SimpleNamespace:
    # Insert before importing either application code or its reusable test fixture.
    sys.path[:0] = [str(source), str(source.parent)]
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    modules = {
        "fixture": "tests.ui.tools.test_unified_task_progress",
        "identity": "transbridge.application.io.identity",
        "budget": "transbridge.application.translation.ai_request_budget",
        "entry": "transbridge.converter.translation_entry",
        "collection": "transbridge.converter.translation_entry_collection",
        "preferences": "transbridge.config.ui_preferences",
        "builtins": "transbridge.ui.foundation.builtins",
        "theme": "transbridge.ui.foundation.theme_service",
        "scope": "transbridge.ui.tools.ai_translator.task_scope",
        "source_execution": "transbridge.ui.tools.ai_translator.source_execution",
        "task_progress": "transbridge.ui.tools.ai_translator.task_progress",
        "task_run": "transbridge.ui.tools.ai_translator.task_run",
        "task_worker": "transbridge.ui.tools.ai_translator.task_worker",
    }
    loaded = {name: importlib.import_module(module) for name, module in modules.items()}
    for name, module in loaded.items():
        expected = source.parent / "tests" if name == "fixture" else source
        if not Path(module.__file__).resolve().is_relative_to(expected):
            raise RuntimeError(f"Import escaped --source: {module.__file__}")
    loaded["qt_core"] = importlib.import_module("PyQt6.QtCore")
    loaded["qt_widgets"] = importlib.import_module("PyQt6.QtWidgets")
    return SimpleNamespace(**loaded)


def _create_window(runtime, entry_count: int):
    session = runtime.fixture._Session()
    tasks = []
    for source_index, name in enumerate(("first", "second")):
        count = entry_count // 2 + (entry_count % 2 if source_index == 0 else 0)
        entries = tuple(
            runtime.entry.TranslationEntry(
                str(index),
                str(index),
                f"Synthetic original {index}",
                "",
                0,
                "NPC_:FULL",
                entry_key=runtime.identity.EntryKey(runtime.identity.SourceNamespace(name), str(index)),
            )
            for index in range(count)
        )
        tasks.append(
            runtime.scope.SourceTask(
                name, f"{name}.esp", None, runtime.collection.TranslationEntryCollection(entries), entries, ()
            )
        )
    session.tasks = tuple(tasks)
    session.project_dir = session.history_dir = None
    request = SimpleNamespace(
        run_id="synthetic-feedback-benchmark",
        config=SimpleNamespace(mixed_execution_order="sequential", max_concurrent=4),
        spec=SimpleNamespace(execution_profile=SimpleNamespace(summary="翻译 → 校对", preview_enabled=False)),
        request_budget=runtime.budget.AiRequestBudget(4),
    )
    activity = SimpleNamespace(progress=lambda *args: None, bind_worker=lambda *args: None, close=lambda: None)
    started = perf_counter()
    window = runtime.task_progress.AiTaskProgressWindow(request, session, activity)
    return window, perf_counter() - started


def _measure(runtime, app, *, entry_count: int, event_count: int, gui_stall_ms=0, waves=1) -> dict:
    window, creation_s = _create_window(runtime, entry_count)
    run = window.run
    run.preparing = False
    window.show()
    window.logs.setPlainText("\n".join(f"已处理记录 {index}" for index in range(3000)))
    for _ in range(5):
        app.processEvents()
    counts = Counter()
    last_progress = {}
    callback_threads = set()
    producer_gate = threading.Barrier(2)
    expected = {"first": event_count // 2 + event_count % 2, "second": event_count // 2}

    class BurstExecutor:
        def __init__(self, request, *, progress, log, **kwargs):
            self.progress, self.log = progress, log

        def execute(self, task):
            producer_gate.wait(timeout=5)
            total = expected[task.key]
            wave_size = max(1, total // waves)
            for index in range(1, total + 1):
                self.progress(task.key, "proofread", index, total, f"正在校对第 {index} 条")
                self.log(task.key, f"正在校对第 {index} 条，检查名称和术语一致性。")
                if waves > 1 and index % wave_size == 0:
                    sleep(0.05)  # Real monitor cadence, rather than an artificial signal flood.
            return runtime.source_execution.SourceOutcome(task)

    original_progress = run._progress

    def receive_progress(key, stage, current, total, message):
        callback_threads.add(threading.get_ident())
        counts["progress_callbacks"] += 1
        last_progress[key] = current
        original_progress(key, stage, current, total, message)

    def receive_logs(text):
        callback_threads.add(threading.get_ident())
        counts["log_widget_insertions"] += 1
        counts["log_lines"] += len(text.splitlines())

    def completed(outcomes):
        callback_threads.add(threading.get_ident())
        if len(outcomes) != 2 or any(outcome.error for outcome in outcomes):
            raise RuntimeError(f"Synthetic source failed: {[outcome.error for outcome in outcomes]}")
        counts["completed"] += 1

    def finished(worker):
        counts["finished"] += 1

    run._progress = receive_progress
    run._completed = completed
    run._worker_finished = finished
    run._record = lambda: None
    run.logged.connect(receive_logs)
    worker_type = runtime.task_worker.AiTaskWorker

    def worker_factory(*args, **kwargs):
        return worker_type(*args, executor_factory=BurstExecutor, **kwargs)

    ticks = [perf_counter()]
    heartbeat = runtime.qt_core.QTimer()
    heartbeat.setInterval(10)
    heartbeat.timeout.connect(lambda: ticks.append(perf_counter()))
    heartbeat.start()
    event_loop_calls = []
    started = perf_counter()
    try:
        with patch.object(runtime.task_run, "AiTaskWorker", worker_factory):
            run._start(window.session.tasks)
            worker = run.worker
            withheld_started = perf_counter()
            if gui_stall_ms:
                sleep(gui_stall_ms / 1000)
                if not worker.wait(30_000):
                    raise TimeoutError("Synthetic producer did not finish during the GUI stall")
            recovery_started = perf_counter()
            # Exclude the deliberate GUI stall from natural heartbeat latency.
            ticks[:] = [recovery_started]
            deadline = perf_counter() + 60
            while not (counts["completed"] and counts["finished"]):
                before = perf_counter()
                app.processEvents()
                event_loop_calls.append(perf_counter() - before)
                if perf_counter() > deadline:
                    raise TimeoutError("Synthetic worker feedback did not settle within 60 seconds")
                sleep(0.001)
            app.processEvents()
        elapsed = perf_counter() - started
        ticks.append(perf_counter())
        assert last_progress == expected, (last_progress, expected)
        assert callback_threads == {threading.get_ident()}, callback_threads
        assert counts["completed"] == counts["finished"] == 1, counts
        if event_count <= 3000 or not hasattr(worker, "log_batch"):
            # Larger batched bursts may intentionally overflow the bounded display buffer.
            assert counts["log_lines"] == event_count + 2, counts  # Two source-start notices.
        return {
            "window_creation_s": creation_s,
            "feedback_delivery_s": elapsed,
            "after_stall_delivery_s": perf_counter() - recovery_started,
            "withheld_gui_s": recovery_started - withheld_started if gui_stall_ms else 0,
            "longest_process_events_s": max(event_loop_calls),
            "heartbeat_max_gap_s": max(b - a for a, b in zip(ticks, ticks[1:])),
            "counts": dict(counts),
            "last_progress": last_progress,
            "log_blocks": window.logs.document().blockCount(),
            "batched_worker_logs": hasattr(worker, "log_batch"),
        }
    finally:
        heartbeat.stop()
        worker = run.worker
        if worker is not None:
            if worker.isRunning():
                worker.stop()
                if not worker.wait(5000):
                    raise RuntimeError("Synthetic worker did not stop")
            run.worker = None
            worker.deleteLater()
        window.dispose()
        window.deleteLater()
        app.processEvents()
        runtime.qt_core.QCoreApplication.sendPostedEvents(None, runtime.qt_core.QEvent.Type.DeferredDelete)


def main() -> int:
    args = _arguments()
    source = args.source.resolve()
    runtime = _load_runtime(source)
    app = runtime.qt_widgets.QApplication.instance() or runtime.qt_widgets.QApplication([])
    preferences = SimpleNamespace(
        load=lambda: SimpleNamespace(
            theme_mode=runtime.preferences.ThemeMode.LIGHT,
            theme_id=runtime.preferences.DEFAULT_THEME_ID,
            diagnostics=(),
        )
    )
    theme = runtime.theme.ThemeService(app, runtime.builtins.create_builtin_registry(), preferences)
    theme.start()
    try:
        samples = [
            _measure(
                runtime,
                app,
                entry_count=args.entries,
                event_count=args.events,
                gui_stall_ms=args.gui_stall_ms,
                waves=args.waves,
            )
            for _ in range(args.repetitions)
        ]
    finally:
        theme.close()
    result = {
        "source": str(source),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "qt_platform": app.platformName(),
        "entries": args.entries,
        "sources": 2,
        "event_pairs": args.events,
        "repetitions": args.repetitions,
        "forced_gui_stall_ms": args.gui_stall_ms,
        "producer_waves": args.waves,
        "excluded": ["API", "commit", "snapshot", "task record", "external TaskRuntime adapter"],
        "summary": {
            name: {
                "median": statistics.median(sample[name] for sample in samples),
                "minimum": min(sample[name] for sample in samples),
                "maximum": max(sample[name] for sample in samples),
            }
            for name in (
                "window_creation_s",
                "feedback_delivery_s",
                "after_stall_delivery_s",
                "longest_process_events_s",
                "heartbeat_max_gap_s",
            )
        },
        "samples": samples,
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
