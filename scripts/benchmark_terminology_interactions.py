"""Offline Qt interaction benchmarks with the original terminology audit inputs.

No production code is patched. Input construction, imports and widget disposal are
outside measurements. All actions run on the QApplication thread. Each timed
action reports synchronous callback time and time through processEvents (paint).
Search uses QLineEdit.insert, so actual textChanged callbacks run for every key.
Index preparation is excluded, but debounce and final result delivery are included
in completion_ms. max_event_loop_gap_ms is a 10 ms QTimer probe, not paint FPS.
Example: uv run python scripts/benchmark_terminology_interactions.py --output <new-file.jsonl>
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import gc
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
from time import perf_counter
from types import SimpleNamespace
import uuid

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
SCENARIOS = (
    "import_build",
    "import_rows",
    "import_open",
    "import_search",
    "source_render",
    "source_search",
    "diff_open",
    "diff_search",
    "manager_open",
    "manager_edit",
)


def emit(args, kind, **values):
    payload = dict(session=args.session, kind=kind, **values)
    line = json.dumps(payload, ensure_ascii=False)
    with args.output.open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")
    print(line, flush=True)


def measure(app, action):
    start = perf_counter()
    result = action()
    returned = perf_counter()
    app.processEvents()
    finished = perf_counter()
    return result, {"sync_ms": (returned - start) * 1000, "total_ms": (finished - start) * 1000}


def wait_ready(app, condition, timeout=30):
    deadline = perf_counter() + timeout
    while not condition():
        if perf_counter() >= deadline:
            raise TimeoutError("terminology interaction did not finish")
        app.processEvents()
        time.sleep(0.001)
    app.processEvents()


def fixtures(size):
    from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
    from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
    from transbridge.application.terminology_profiles import (
        InMemoryTerminologyProfileRepository,
        ProfileTermMapping,
        TerminologyProfileContent,
        TerminologyProfileImportService,
        TerminologyProfileService,
        TerminologySourceEntry,
        TerminologySourceSnapshot,
    )

    decisions = tuple(
        TermDecision(
            f"term-{index:06d}",
            "project",
            "variant",
            f"Term {index:06d}",
            f"term {index:06d}",
            f"项目默认译名 {index:06d}",
            scope=TermScope.plugin("Skyrim.esm") if index % 10 == 0 else TermScope(),
            status=DecisionStatus.ADOPTED,
            notes="用于合成基准的术语备注；保留来源上下文。",
            evidence_ids=(f"source-{index:06d}-a", f"source-{index:06d}-b"),
        )
        for index in range(size)
    )
    mappings = tuple(
        ProfileTermMapping(
            item.original, f"方案译名 {index:06d}", item.translation, item.scope.kind.value, item.scope.plugin_id
        )
        for index, item in enumerate(decisions)
    )
    profiles = TerminologyProfileService(InMemoryTerminologyProfileRepository(), new_id=lambda: "profile")
    importer = TerminologyProfileImportService(profiles)
    sources = [
        TerminologySourceEntry(item.original, f"来源译名 {index:06d}")
        for index, item in enumerate(decisions)
        if index % 5
    ]
    sources.extend(
        TerminologySourceEntry(f"Source only {index:06d}", f"来源独有 {index:06d}") for index in range(size // 10)
    )
    sources.extend(
        TerminologySourceEntry(item.original, f"冲突译名 {index:06d}")
        for index, item in enumerate(decisions)
        if index % 20 == 1
    )
    source = TerminologySourceSnapshot.capture("synthetic", "合成外部来源", sources)
    base = EffectiveTerminologySnapshot(
        "project",
        "variant",
        EffectiveSnapshotStatus.READY,
        version_id="base",
        content_digest="synthetic-base",
        decisions=decisions,
    )
    return SimpleNamespace(
        decisions=decisions,
        mappings=mappings,
        profiles=profiles,
        importer=importer,
        source=source,
        base=base,
        content=TerminologyProfileContent(mappings=mappings),
    )


def worker(args):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6 import sip
    from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, Qt, QThreadPool, QTimer
    from PyQt6.QtWidgets import QApplication, QWidget

    app = QApplication.instance() or QApplication([])
    warm = QWidget()
    warm.show()
    app.processEvents()
    sip.delete(warm)
    data = fixtures(args.size)
    emit(
        args,
        "worker",
        scenario=args.scenario,
        size=args.size,
        qt=QT_VERSION_STR,
        pyqt=PYQT_VERSION_STR,
        synthetic=True,
        platform="offscreen",
        repository="in-memory",
        setup_excluded=True,
    )
    records = []
    scenario = args.scenario
    preview = None
    widget = None
    if scenario.startswith("import_") and scenario != "import_build":
        preview = data.importer.preview("project", "variant", data.base, data.source)
    if scenario.startswith("source_"):
        from transbridge.application.terminology_profiles.browse_queries import ProfileMappingRow, ProfileMappingState
        from transbridge.ui.tools.terminology.source_browser import SourceBrowser

        context = SimpleNamespace(
            profile_id="profile",
            diagnostics=(),
            mapping_rows=tuple(
                ProfileMappingRow(mapping.term_key, mapping, decision, ProfileMappingState.EFFECTIVE)
                for mapping, decision in zip(data.mappings, data.decisions, strict=True)
            ),
        )
        widget = SourceBrowser()
        widget.resize(1100, 760)
        widget.show()
        app.processEvents()
        if scenario == "source_search":
            widget.render_context(context)
    if scenario.startswith("diff_"):
        from transbridge.application.terminology.models import CanonicalChange, CanonicalDiff, ChangeType
        from transbridge.ui.tools.terminology.change_review_dialog import ChangeReviewDialog

        changes = tuple(
            CanonicalChange(
                f"change-{index:06d}",
                ChangeType.TRANSLATION_CHANGED,
                item.term_id,
                "before",
                "after",
                True,
                item,
                replace(item, translation=f"更新后的译名 {index:06d}"),
            )
            for index, item in enumerate(data.decisions)
        )
        comparison = CanonicalDiff("before-version", "after-version", "synthetic-diff", changes)
    if scenario.startswith("manager_"):
        from transbridge.ui.tools.terminology_profiles.manager_dialog import TerminologyProfileManagerDialog

        profile = data.profiles.create_with_content("project", "合成方案", data.content)
        data.profiles.publish(profile.profile_id, expected_draft_revision=profile.draft_revision)
    if scenario == "import_search":
        from transbridge.ui.tools.terminology_profiles.import_dialog import TerminologySourceImportDialog

        widget = TerminologySourceImportDialog(preview, "合成方案")
    elif scenario == "diff_search":
        widget = ChangeReviewDialog(comparison)
    elif scenario == "manager_edit":
        widget = TerminologyProfileManagerDialog(data.profiles, "project")
    if widget is not None:
        widget.show()
        app.processEvents()
    search_worker = None
    if scenario.endswith("_search"):
        search_worker = (
            widget.filtered.search
            if scenario == "import_search"
            else (widget.changes._search if scenario == "diff_search" else widget._search)
        )
        wait_ready(app, lambda: search_worker._index is not None or search_worker.last_error is not None)
        if search_worker.last_error is not None:
            raise search_worker.last_error

    for repeat in range(1, args.repeats + 1):
        gc.collect()
        app.processEvents()
        if scenario.endswith("_search"):
            edit = (
                widget.search_edit
                if scenario == "import_search"
                else (widget.changes.search if scenario == "diff_search" else widget.search)
            )
            edit.clear()
            app.processEvents()
            samples = []
            query = f"Term {args.size - 1:06d}"
            ticks = [perf_counter()]
            timer = QTimer()
            timer.setInterval(10)
            timer.timeout.connect(lambda: ticks.append(perf_counter()))
            timer.start()
            started = perf_counter()
            for character in query:
                _, timed = measure(app, lambda character=character: edit.insert(character))
                samples.append(timed)
            wait_ready(app, lambda: not search_worker.is_pending)
            ticks.append(perf_counter())
            timer.stop()
            if search_worker.last_error is not None:
                raise search_worker.last_error
            filtered = widget.changes.filtered if scenario == "diff_search" else widget.filtered
            assert filtered.rowCount() == 1, (scenario, filtered.rowCount())
            record = {
                "sync_ms": sum(item["sync_ms"] for item in samples),
                "total_ms": sum(item["total_ms"] for item in samples),
                "worst_key_sync_ms": max(item["sync_ms"] for item in samples),
                "worst_key_total_ms": max(item["total_ms"] for item in samples),
                "key_count": len(samples),
                "completion_ms": (perf_counter() - started) * 1000,
                "max_event_loop_gap_ms": max(right - left for left, right in zip(ticks, ticks[1:])) * 1000,
            }
        else:
            disposable = None
            if scenario == "import_build":
                result, record = measure(
                    app, lambda: data.importer.preview("project", "variant", data.base, data.source)
                )
                record["result_rows"] = result.base_mapping_count + result.source_only_term_count
            elif scenario == "import_rows":
                from transbridge.application.terminology_profiles.import_preview import import_preview_rows

                result, record = measure(app, lambda: import_preview_rows(preview))
                record["result_rows"] = len(result)
            elif scenario == "import_open":
                from transbridge.ui.tools.terminology_profiles.import_dialog import TerminologySourceImportDialog

                def action():
                    result = TerminologySourceImportDialog(preview, "合成方案")
                    result.show()
                    return result

                disposable, record = measure(app, action)
                record["result_rows"] = disposable.filtered.rowCount()
            elif scenario == "source_render":
                _, record = measure(app, lambda: widget.render_context(context))
                record["result_rows"] = widget.filtered.rowCount()
            elif scenario == "diff_open":

                def action():
                    result = ChangeReviewDialog(comparison)
                    result.show()
                    return result

                disposable, record = measure(app, action)
                record["result_rows"] = disposable.changes.filtered.rowCount()
            elif scenario == "manager_open":

                def action():
                    result = TerminologyProfileManagerDialog(data.profiles, "project")
                    result.show()
                    return result

                disposable, record = measure(app, action)
                record["result_rows"] = disposable.mapping_table.model().rowCount()
            elif scenario == "manager_edit":
                model = widget.mapping_table.model()
                _, record = measure(
                    app,
                    lambda: model.setData(
                        model.index(args.size - 1, 2), f"单元格新译名 {repeat}", Qt.ItemDataRole.EditRole
                    ),
                )
                record["result_rows"] = model.rowCount()
            else:
                raise ValueError(scenario)
            if disposable is not None:
                sip.delete(disposable)
                app.processEvents()
        records.append(record)

    summary = {}
    for metric in (
        "sync_ms",
        "total_ms",
        "worst_key_sync_ms",
        "worst_key_total_ms",
        "completion_ms",
        "max_event_loop_gap_ms",
    ):
        if metric in records[0]:
            values = [record[metric] for record in records]
            summary[metric] = {"median": statistics.median(values), "min": min(values), "max": max(values)}
    emit(args, "summary", scenario=scenario, size=args.size, repeats=len(records), samples=records, **summary)
    if widget is not None:
        sip.delete(widget)
    if not QThreadPool.globalInstance().waitForDone(30_000):
        raise TimeoutError("terminology search worker did not stop")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenarios",
        nargs="+",
        choices=SCENARIOS,
        default=["import_search", "source_search", "diff_search", "manager_open", "manager_edit"],
    )
    parser.add_argument("--sizes", nargs="+", type=int, default=[1000, 10000, 50000])
    parser.add_argument("--manager-sizes", nargs="+", type=int, default=[200, 1000, 2000])
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=180, help="Seconds per scenario/size group, including setup")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--scenario", choices=SCENARIOS)
    parser.add_argument("--size", type=int)
    parser.add_argument("--session", default=str(uuid.uuid4()))
    args = parser.parse_args()
    args.output = args.output.resolve()
    if args.repeats < 5 or any(size < 1 for size in (*args.sizes, *args.manager_sizes)):
        parser.error("at least five repetitions and positive sizes are required")
    if args.worker:
        worker(args)
        return 0
    if args.output.exists():
        parser.error("output must be a new file")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    emit(
        args,
        "environment",
        python=sys.version,
        executable=sys.executable,
        os=platform.platform(),
        cpu=platform.processor(),
        logical_cores=os.cpu_count(),
        repeats=args.repeats,
        command=sys.argv,
        note="Synthetic offscreen Qt; setup excluded; search completion includes debounce",
    )
    failed = False
    for scenario in args.scenarios:
        sizes = args.manager_sizes if scenario.startswith("manager_") else args.sizes
        for size in sizes:
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--worker",
                "--scenario",
                scenario,
                "--size",
                str(size),
                "--repeats",
                str(args.repeats),
                "--output",
                str(args.output),
                "--session",
                args.session,
            ]
            emit(args, "started", scenario=scenario, size=size, timeout_seconds=args.timeout)
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=args.timeout,
                    check=False,
                    env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                )
                print(result.stdout, end="", flush=True)
                if result.returncode:
                    failed = True
                    emit(
                        args,
                        "failed",
                        scenario=scenario,
                        size=size,
                        exit_code=result.returncode,
                        error=result.stderr[-8000:],
                    )
            except subprocess.TimeoutExpired:
                failed = True
                emit(
                    args,
                    "timeout",
                    scenario=scenario,
                    size=size,
                    timeout_seconds=args.timeout,
                    note="No timing median is inferred from an incomplete group",
                )
    print(f"RAW_RESULTS={args.output}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
