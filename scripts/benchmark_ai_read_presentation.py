"""Measure actual Qt delivery/layout of synthetic long-line logs and diagnostics.

The default 2 MiB ASCII input contains a 1 MiB unbroken line. All files live in
an automatically removed temporary directory; no API or user data is accessed.
Both elapsed GUI delivery and setPlainText plus synchronous repaint are timed.
"""

from __future__ import annotations

import argparse
import faulthandler
import importlib
import json
from pathlib import Path
import platform
import shutil
import statistics
import sys
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4

from benchmark_ai_task_lifecycle import _assert_imports, _configure_font, _load_runtime, _measure_phase, _positive_int


def _wait(runtime, ready, predicate, parent):
    timer = runtime.core.QTimer(parent)
    timer.setInterval(5)

    def check():
        if predicate():
            timer.stop()
            timer.deleteLater()
            ready(None)

    timer.timeout.connect(check)
    timer.start()


def _payload(size):
    line = "Synthetic diagnostic message: " + "x" * 68 + "\n"
    long_line = "L" * (size // 2) + "\n"
    return (long_line + line * ((size - len(long_line)) // len(line) + 1))[:size]


def _instrument(editor, label):
    original = editor.setPlainText
    calls = []

    def apply(text):
        print(f"{label}: setPlainText {len(text)} characters", file=sys.stderr, flush=True)
        started = perf_counter()
        original(text)
        editor.viewport().repaint()
        calls.append({"characters": len(text), "set_and_repaint_s": perf_counter() - started})
        print(f"{label}: delivery returned in {calls[-1]['set_and_repaint_s']:.6f}s", file=sys.stderr, flush=True)

    editor.setPlainText = apply
    return calls


def _settle(runtime, widget, predicate):
    _measure_phase(runtime, lambda ready, _failed: _wait(runtime, ready, predicate, widget))


def _log_sample(runtime, text, directory):
    module = importlib.import_module("transbridge.ui.tools.ai_translator._llm_log_viewer")
    viewer = module._LLMLogViewer(str(directory))
    viewer.show()
    viewer.stop_auto_refresh()
    reader = getattr(viewer, "_reader", None)
    _settle(runtime, viewer, lambda: reader is None or not reader.busy)
    calls = _instrument(viewer._text_edit, "logs")
    (directory / "workflow.log").write_bytes(text.encode("utf-8"))

    def deliver(ready, _failed):
        viewer._scan_and_refresh()
        _wait(runtime, ready, lambda: bool(calls) and (reader is None or not reader.busy), viewer)

    try:
        _, measurement = _measure_phase(runtime, deliver)
        measurement["set_plain_text"] = calls
        return measurement
    finally:
        viewer.close()
        viewer.deleteLater()


def _diagnostic_sample(runtime, text):
    module = importlib.import_module("transbridge.ui.tools.ai_translator.task_result_dialog")
    source = {"key": "synthetic", "label": "Synthetic.esp", "snapshot": {"entries": [], "diagnostics": []}}
    dialog = module.TaskResultDialog({"sources": [source]})
    dialog.show()
    _settle(runtime, dialog, lambda: not getattr(dialog, "_projecting", False))
    calls = _instrument(dialog.diagnostics, "diagnostics")
    # Use a new immutable source object, matching a newly opened report.
    dialog.record["sources"][0] = {
        **source,
        "snapshot": {"entries": [], "diagnostics": [{"code": "SYNTHETIC", "message": text, "details": {}}]},
    }

    def deliver(ready, _failed):
        dialog._populate()
        _wait(runtime, ready, lambda: bool(calls) and not getattr(dialog, "_projecting", False), dialog)

    try:
        _, measurement = _measure_phase(runtime, deliver)
        measurement["set_plain_text"] = calls
        return measurement
    finally:
        dialog.close()
        dialog.deleteLater()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1] / "src")
    parser.add_argument("--bytes", type=_positive_int, default=2 * 1024 * 1024)
    parser.add_argument("--repetitions", type=_positive_int, default=5)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--temp-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--trace-after", type=_positive_int, help="Dump Python stacks if delivery stalls this many seconds"
    )
    args = parser.parse_args()
    source = args.source.resolve()
    runtime = _load_runtime(source)
    if args.trace_after:
        faulthandler.dump_traceback_later(args.trace_after)
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
    text = _payload(args.bytes)
    samples = []
    temp_root = args.temp_root.resolve()
    directory = temp_root / f".tmp-read-presentation-{uuid4().hex}"
    directory.mkdir()
    if not directory.resolve().is_relative_to(temp_root):
        raise RuntimeError("Synthetic temporary directory escaped its requested root")
    try:
        for index in range(args.repetitions):
            log_dir = directory / str(index)
            log_dir.mkdir()
            samples.append({
                "logs": _log_sample(runtime, text, log_dir),
                "diagnostics": _diagnostic_sample(runtime, text),
            })
            app.processEvents()
            runtime.core.QCoreApplication.sendPostedEvents(None, runtime.core.QEvent.Type.DeferredDelete)
    finally:
        theme.close()
        if directory.resolve().is_relative_to(temp_root):
            shutil.rmtree(directory)
    _assert_imports(source)
    report = {
        "source": str(source),
        "python": platform.python_version(),
        "qt_platform": app.platformName(),
        "font": font,
        "bytes": args.bytes,
        "long_line_characters": args.bytes // 2,
        "repetitions": args.repetitions,
        "summary": {
            phase: {
                metric: {
                    "median": statistics.median(sample[phase][metric] for sample in samples),
                    "minimum": min(sample[phase][metric] for sample in samples),
                    "maximum": max(sample[phase][metric] for sample in samples),
                }
                for metric in ("elapsed_s", "heartbeat_max_gap_s")
            }
            for phase in ("logs", "diagnostics")
        },
        "samples": samples,
    }
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
