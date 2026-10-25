"""Measure offline AI session construction and result ingestion on a real Qt loop.

Use --source to alternate the current src tree and an isolated baseline src.
Both paths include TaskSession plus task-window/ledger construction. The current
path uses create_async when available; the baseline uses its synchronous public
constructor. Valid synthetic proofreading results stop at pending confirmation.
No API, commit, snapshot persistence, task record or recovery write is performed.

The benchmark reports total elapsed time separately from the longest GUI timer
gap. Thread probes verify where ledger initialization and ingestion execute.
--render-dir optionally saves genuine offscreen Qt windows using synthetic data.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from functools import wraps
import gc
import importlib
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import threading
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch


def _positive_int(value):
    value = int(value)
    if value < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def _arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1] / "src")
    parser.add_argument("--entries", type=_positive_int, default=20_000)
    parser.add_argument("--repetitions", type=_positive_int, default=5)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--render-dir", type=Path)
    args = parser.parse_args()
    if args.entries < 2:
        parser.error("--entries must allow at least one entry per source")
    return args


def _assert_imports(source):
    for name, module in tuple(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if (
            (name == "transbridge" or name.startswith("transbridge."))
            and path
            and not Path(path).resolve().is_relative_to(source)
        ):
            raise RuntimeError(f"Import escaped --source: {name}: {path}")


def _configure_font(app):
    """Load a real CJK font in the isolated offscreen process when available."""
    gui = importlib.import_module("PyQt6.QtGui")
    path = Path("C:/Windows/Fonts/msyh.ttc")
    if path.exists():
        font_id = gui.QFontDatabase.addApplicationFont(str(path))
        families = gui.QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        if not families:
            raise RuntimeError(f"Could not load benchmark CJK font: {path}")
        app.setFont(gui.QFont(families[0], 9))
        return str(path)
    return app.font().family()


def _load_runtime(source):
    sys.path.insert(0, str(source))
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    names = {
        "identity": "transbridge.application.io.identity",
        "entry": "transbridge.converter.translation_entry",
        "collection": "transbridge.converter.translation_entry_collection",
        "scope": "transbridge.ui.tools.ai_translator.task_scope",
        "session": "transbridge.ui.tools.ai_translator.task_session",
        "ledger": "transbridge.ui.tools.ai_translator.task_entry_results",
        "progress": "transbridge.ui.tools.ai_translator.task_progress",
        "run": "transbridge.ui.tools.ai_translator.task_run",
        "outcome": "transbridge.ui.tools.ai_translator.source_execution",
        "preferences": "transbridge.config.ui_preferences",
        "builtins": "transbridge.ui.foundation.builtins",
        "adapters": "transbridge.ui.foundation.adapters",
        "theme": "transbridge.ui.foundation.theme_service",
        "core": "PyQt6.QtCore",
        "widgets": "PyQt6.QtWidgets",
    }
    runtime = SimpleNamespace(**{key: importlib.import_module(name) for key, name in names.items()})
    _assert_imports(source)
    return runtime


def _synthetic_inputs(runtime, count):
    tasks, slots = [], {}
    for source_index, name in enumerate(("first", "second")):
        size = count // 2 + (count % 2 if source_index == 0 else 0)
        entries = tuple(
            runtime.entry.TranslationEntry(
                id=f"{name}-{index}",
                key=str(index),
                original=f"Synthetic item {name} {index}",
                translation=f"原译文 {name} {index}",
                stage=1,
                context="WEAP:FULL",
                entry_key=runtime.identity.EntryKey(runtime.identity.SourceNamespace(name), str(index)),
                metadata=(("benchmark", {"sequence": index, "tags": ["synthetic", "name"]}),),
            )
            for index in range(size)
        )
        collection = runtime.collection.TranslationEntryCollection(entries)
        task = runtime.scope.SourceTask(name, f"{name}.esp", None, collection, (), entries)
        tasks.append(task)
        slots[name] = SimpleNamespace(collection=collection, label=task.label, esp_path=None)
    ctx = SimpleNamespace(
        active_version_identity=("synthetic-project", "synthetic-variant"),
        project_revision=1,
        variant_revision=1,
        project_variants=(),
        uses_authoritative_projection=True,
        slots=slots,
    )
    spec = SimpleNamespace(
        run_id="synthetic-lifecycle",
        mode="polish",
        execution_profile=SimpleNamespace(summary="校对", preview_enabled=True, enable_proofread=True),
    )
    request = SimpleNamespace(
        run_id=spec.run_id,
        spec=spec,
        config=SimpleNamespace(mixed_execution_order="sequential", max_concurrent=4),
    )
    return ctx, tuple(tasks), request


def _measure_phase(runtime, action):
    """Run actions on an actual event loop, without polling/sleep pacing artifacts."""
    loop = runtime.core.QEventLoop()
    heartbeat, watchdog = runtime.core.QTimer(), runtime.core.QTimer()
    heartbeat.setInterval(10)
    watchdog.setSingleShot(True)
    ticks, errors, values, dispatched = [], [], [], []
    finished_at = []

    def ready(value):
        if not finished_at:
            values.append(value)
            finished_at.append(perf_counter())
            runtime.core.QTimer.singleShot(20, loop.quit)

    def failed(error):
        errors.append(str(error))
        ready(None)

    def invoke():
        began = perf_counter()
        try:
            action(ready, failed)
        except Exception as exc:
            failed(exc)
        finally:
            dispatched.append(perf_counter() - began)

    heartbeat.timeout.connect(lambda: ticks.append(perf_counter()))
    watchdog.timeout.connect(lambda: failed("Synthetic phase timed out after 120 seconds"))
    started = perf_counter()
    heartbeat.start()
    watchdog.start(120_000)
    runtime.core.QTimer.singleShot(0, invoke)
    loop.exec()
    heartbeat.stop()
    watchdog.stop()
    if errors:
        raise RuntimeError("; ".join(errors))
    ended = finished_at[0]
    observed = [started, *(tick for tick in ticks if tick <= ended), ended]
    return values[0], {
        "elapsed_s": ended - started,
        "dispatch_callback_s": dispatched[0],
        "heartbeat_max_gap_s": max(b - a for a, b in zip(observed, observed[1:])),
        "heartbeat_count": len(observed) - 2,
    }


def _probe(original, name, probes):
    @wraps(original)
    def measured(*args, **kwargs):
        started = perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            probes.append({
                "operation": name,
                "thread_id": threading.get_ident(),
                "elapsed_s": perf_counter() - started,
            })

    return measured


def _build_window(runtime, request, session, theme_view):
    activity = SimpleNamespace(close=lambda: None, progress=lambda *_: None)
    window = runtime.progress.AiTaskProgressWindow(request, session, activity, theme_view=theme_view)
    window.run.preview_requested.disconnect(window._offer_preview)
    window.show()
    return window


def _candidate(entry):
    return SimpleNamespace(
        accepted=True,
        confidence=0.9,
        polished_translation=f"校对结果 {entry.id}",
        refined_translation=f"修正译文 {entry.id}",
        processing_status="completed",
        verdict="pass",
        note="离线合成候选",
        issues=(),
    )


def _measure(runtime, app, count, theme_view, render_dir):
    ctx, tasks, request = _synthetic_inputs(runtime, count)
    probes, owned = [], []
    async_create = getattr(runtime.session.TaskSession, "create_async", None)

    def startup(ready, failed):
        def prepared(session):
            try:
                window = _build_window(runtime, request, session, theme_view)
                owned.append(window)
                ready(window)
            except Exception as exc:
                failed(exc)

        if async_create is None:
            prepared(runtime.session.TaskSession(ctx, tasks, request.spec))
        else:
            async_create(ctx, tasks, request.spec, on_success=prepared, on_error=failed)

    ledger_type = runtime.ledger.TaskEntryResults
    capture_type = None
    module_name = "transbridge.ui.tools.ai_translator.task_session_preparation"
    if importlib.util.find_spec(module_name) is not None:
        capture_type = importlib.import_module(module_name).SessionCapture
    try:
        with ExitStack() as stack:
            for name in ("__init__", "ingest"):
                stack.enter_context(patch.object(ledger_type, name, _probe(getattr(ledger_type, name), name, probes)))
            if capture_type is not None:
                stack.enter_context(
                    patch.object(
                        capture_type,
                        "require_current",
                        _probe(capture_type.require_current, "final_session_guard", probes),
                    )
                )
            # Persistence is a separate scenario. Do not write any project data.
            stack.enter_context(patch.object(runtime.run.AiTaskRun, "_record", lambda _self: None))
            window, startup_sample = _measure_phase(runtime, startup)
            run = window.run
            run.preparing = False
            outcomes = tuple(
                runtime.outcome.SourceOutcome(
                    task, polish={entry.id: _candidate(entry) for entry in task.polish_entries}
                )
                for task in run.session.tasks
            )
            worker = SimpleNamespace(was_cancelled=False, deleteLater=lambda: None)
            run.worker = worker
            run.request_state.bind(worker)

            def ingest(ready, failed):
                def settled():
                    preparation = getattr(run, "result_preparation", None)
                    if run.worker is None and not (preparation is not None and preparation.busy):
                        if run.state == "pending_confirmation":
                            ready(None)
                        elif run.state == "failed":
                            failed(run.status)

                run.changed.connect(settled)
                run._completed(outcomes)
                run._worker_finished(worker)
                settled()

            _, ingest_sample = _measure_phase(runtime, ingest)
            assert len(run.entries.ready_keys) == count
            assert all(row.decision == "pending" and not row.applied for row in run.entries.entries.values())
            assert not run.session.applied_keys
            if render_dir is not None:
                _render(runtime, app, window, outcomes, render_dir, theme_view)
        gui_thread = threading.get_ident()
        for probe in probes:
            probe["thread"] = "gui" if probe.pop("thread_id") == gui_thread else "background"
        return {
            "startup": startup_sample,
            "ingest": ingest_sample,
            "probes": probes,
            "async_session": async_create is not None,
        }
    finally:
        for window in owned:
            window.run.worker = None
            window.run.preparing = False
            window.dispose()
            window.run.deleteLater()
            window.deleteLater()
        app.processEvents()
        runtime.core.QCoreApplication.sendPostedEvents(None, runtime.core.QEvent.Type.DeferredDelete)
        gc.collect()


def _render(runtime, app, window, outcomes, directory, theme_view):
    """Use independent offscreen widgets, never the user's running application."""
    directory.mkdir(parents=True, exist_ok=True)
    window.grab().save(str(directory / "progress.png"))
    preview_type = importlib.import_module(
        "transbridge.ui.tools.ai_translator._polish_preview_dialog"
    )._PolishPreviewDialog
    result_type = importlib.import_module("transbridge.ui.tools.ai_translator.task_result_dialog").TaskResultDialog
    log_type = importlib.import_module("transbridge.ui.tools.ai_translator._llm_log_viewer")._LLMLogViewer
    entries = list(outcomes[0].task.polish_entries[:200])
    preview = preview_type(entries, outcomes[0].polish, theme_view=theme_view)
    record = {
        "created_at": "2026-10-24T12:00:00",
        "version_label": "离线合成项目",
        "applied": False,
        "sources": [
            {
                "key": outcome.task.key,
                "label": outcome.task.label,
                "snapshot": {
                    "entries": [
                        {
                            "entry_key": {"local_key": entry.key},
                            "original": entry.original,
                            "before": entry.translation,
                            "candidate": f"校对结果 {entry.id}",
                            "report_details": {"result_status": "pending", "note": "离线合成候选"},
                        }
                        for entry in outcome.task.polish_entries[:200]
                    ],
                    "diagnostics": [],
                },
            }
            for outcome in outcomes
        ],
    }
    report = result_type(record, theme_view=theme_view)
    log_dir = directory / "synthetic-logs"
    log_dir.mkdir(exist_ok=True)
    (log_dir / "workflow.log").write_text(
        "离线合成任务\n正在准备项目名称\n全部来源已完成校对\n等待确认候选译文\n", encoding="utf-8"
    )
    logs = log_type(str(log_dir))
    views = (("preview", preview), ("report", report), ("logs", logs))
    for name, view in views:
        view.show()

        def await_view(ready, _failed, view=view):
            timer = runtime.core.QTimer(view)
            timer.setInterval(5)

            def check():
                reader = getattr(view, "_reader", None)
                if (
                    not getattr(view, "_loading", False)
                    and not getattr(view, "_projecting", False)
                    and not (reader and reader.busy)
                ):
                    timer.stop()
                    timer.deleteLater()
                    ready(None)

            timer.timeout.connect(check)
            timer.start()

        _measure_phase(runtime, await_view)
        app.processEvents()
        view.grab().save(str(directory / f"{name}.png"))
        view.close()
        view.deleteLater()


def main():
    args = _arguments()
    source = args.source.resolve()
    runtime = _load_runtime(source)
    app = runtime.widgets.QApplication.instance() or runtime.widgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    preferences = SimpleNamespace(
        load=lambda: SimpleNamespace(
            theme_mode=runtime.preferences.ThemeMode.LIGHT,
            theme_id=runtime.preferences.DEFAULT_THEME_ID,
            diagnostics=(),
        )
    )
    theme = runtime.theme.ThemeService(app, runtime.builtins.create_builtin_registry(), preferences)
    theme.start()
    font = _configure_font(app)
    theme_view = runtime.adapters.ThemeView(theme)
    try:
        samples = []
        for index in range(args.repetitions):
            samples.append(_measure(runtime, app, args.entries, theme_view, args.render_dir if index == 0 else None))
            # The measured frame has now released its live collections and QObject
            # wrappers; do not retain the previous large sample into the next one.
            gc.collect()
    finally:
        theme_view.close()
        theme.close()
    _assert_imports(source)
    report = {
        "source": str(source),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "qt_platform": app.platformName(),
        "font": font,
        "sources": 2,
        "entries": args.entries,
        "repetitions": args.repetitions,
        "heartbeat_interval_ms": 10,
        "scenario": "session-and-window-plus-polish-result-ingest-v1",
        "excluded": ["API", "commit", "snapshot persistence", "task record", "recovery persistence", "scope selection"],
        "summary": {
            phase: {
                metric: {
                    "median": statistics.median(sample[phase][metric] for sample in samples),
                    "minimum": min(sample[phase][metric] for sample in samples),
                    "maximum": max(sample[phase][metric] for sample in samples),
                }
                for metric in ("elapsed_s", "dispatch_callback_s", "heartbeat_max_gap_s")
            }
            for phase in ("startup", "ingest")
        },
        "samples": samples,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
