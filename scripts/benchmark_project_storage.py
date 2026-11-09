"""Measure FR32 through the production Workbench and save commands on synthetic data.

The first run uses a fresh process-local project binding. This script does not
flush the system filesystem cache. Memory values are Python allocations traced
during each measurement. Run the old benchmark from its original checkout to
obtain a comparable pre-FR32 baseline.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from contextlib import contextmanager
import json
import os
from pathlib import Path
import platform
from shutil import rmtree
import sqlite3
import statistics
import sys
from time import perf_counter, sleep
import tracemalloc
from uuid import uuid4

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.application.io.identity import EntryKey, EntryRevision, SourceNamespace
from transbridge.application.io.mutation import EntrySnapshot
from transbridge.application.projects.storage_contracts import ExtractedSource, StoredEntry
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.project_store.paths import project_path
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.persistence.task_store import TaskStore
from transbridge.persistence.v2.ids import ProjectId, VariantId
from transbridge.persistence.v2.variant import VariantEntryState
from transbridge.ui.context import AppContext
from transbridge.ui.coordinators.project_open_operation import _prepare_paged_workbench_view
from transbridge.ui.workbench.step2 import Step2PreviewWidget
from transbridge.ui.workers import ApiWorker

PROJECT = ProjectId("benchmark-project")
VARIANT = VariantId("main")
CONTEXT = RequestContext("benchmark", run_id="project-storage-benchmark")


def sources(count: int, source_count: int):
    for index in range(source_count):
        namespace = SourceNamespace(f"benchmark:{index}")
        entries = []
        for number in range(count // source_count + int(index < count % source_count)):
            original = f"Source {index}, original {number}"
            key = EntryKey(namespace, f"Entry-{number // 2}", original if number % 2 else None)
            translation = "" if number % 5 == 0 else f"Translation {number}"
            stage = 0 if not translation else 1
            entry = EntrySnapshot(
                key,
                key.local_key,
                original,
                translation,
                stage,
                "INFO:NAM1|" + ("context " * (number % 20)),
                (),
                EntryRevision(),
                (),
                (),
            )
            entries.append(StoredEntry(entry, VariantEntryState(key, translation, stage)))
        yield ExtractedSource(
            namespace, f"Synthetic-{index}.json", FormatId.JSON_TRANSBRIDGE, f"{index:064x}", tuple(entries)
        )


def task_history(root: Path, count: int) -> None:
    if not count:
        return
    recovery = TaskStore(root / "task-store-v1").recovery()
    for index in range(count):
        recovery.create({
            "task_id": f"benchmark-task-{index}",
            "project_id": PROJECT.value,
            "variant_id": VARIANT.value,
            "mode": "download",
            "config_digest": "synthetic",
            "state": "completed",
            "created_at": "2026-11-09T00:00:00+08:00",
            "sources": [],
            "result_kind": "download",
            "results_only": True,
        })


def run_worker(application: QApplication, operation: Callable):
    result, errors, ticks = [], [], [perf_counter()]
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(perf_counter()))
    worker = ApiWorker(operation, route_http_errors=False)
    worker.result.connect(result.append)
    worker.error.connect(errors.append)
    timer.start()
    worker.start()
    while worker.isRunning():
        application.processEvents()
        sleep(0.001)
    application.processEvents()
    ticks.append(perf_counter())
    timer.stop()
    worker.deleteLater()
    if errors:
        raise RuntimeError(errors[0])
    if len(result) != 1:
        raise RuntimeError("benchmark worker did not return its measurement")
    return result[0], max((end - start) * 1000 for start, end in zip(ticks, ticks[1:], strict=False))


@contextmanager
def journal_meter(path: Path):
    original = sqlite3.connect
    measures = {"journal_bytes": 0, "rows_changed": 0}
    journal = Path(str(path) + "-journal")

    class MeasuredConnection(sqlite3.Connection):
        def commit(self):
            if journal.is_file():
                measures["journal_bytes"] = max(measures["journal_bytes"], journal.stat().st_size)
            measures["rows_changed"] += self.total_changes
            return super().commit()

    def connect(*args, **options):
        return original(*args, factory=MeasuredConnection, **options)

    sqlite3.connect = connect
    try:
        yield measures
    finally:
        sqlite3.connect = original


def measured_save(application, services, path, changes):
    before = path.read_bytes()
    start = perf_counter()
    with journal_meter(path) as measure:
        result, heartbeat = run_worker(application, lambda: services.gui_project_commands.save(CONTEXT))
    elapsed = (perf_counter() - start) * 1000
    require(result)
    after = path.read_bytes()
    page = int.from_bytes(before[16:18], "big")
    page = 65536 if page == 1 else page
    modified_pages = sum(
        before[offset : offset + page] != after[offset : offset + page]
        for offset in range(0, max(len(before), len(after)), page)
    )
    return {
        "edits": changes,
        "elapsed_ms": elapsed,
        "heartbeat_ms": heartbeat,
        "changed_page_bytes": modified_pages * page,
        **measure,
    }


def measure_open(application, root, store, edits):
    services = build_persistence_v2_services(
        root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "benchmark"
    )
    context = AppContext(
        project_projection=services.project_projection,
        project_commands=services.gui_project_commands,
        runtime_context=CONTEXT,
    )
    workbench = Step2PreviewWidget(context)
    generation = context.begin_project_open()

    def prepare():
        target = require(services.current_project_opener.prepare_path(store.path, CONTEXT, paged=True))
        reader = target.records.paged_read
        return require(
            services.current_project_opener.activate(
                target, CONTEXT, prepare_view=lambda projection: _prepare_paged_workbench_view(reader, projection)
            )
        )

    tracemalloc.start()
    started = perf_counter()
    try:
        opened, heartbeat = run_worker(application, prepare)
        projection = services.project_projection.snapshot()
        view = opened["view_payload"]
        if not context.install_project_open(generation, projection, view.slots, state=view.state.committed(projection)):
            raise RuntimeError("workbench rejected its prepared benchmark project")
        context.finish_project_open(generation)
        first_editable = (perf_counter() - started) * 1000
        first_page = len(workbench._table.render_session.entries)
        logical_rows = workbench._table.rowCount()
        resident_heartbeat = 0.0
        while not workbench._paging.resident_ready:
            tick = perf_counter()
            if tick - started > 180:
                raise TimeoutError("active source did not finish loading")
            application.processEvents()
            sleep(0.001)
            resident_heartbeat = max(resident_heartbeat, (perf_counter() - tick) * 1000)
        resident_ready = (perf_counter() - started) * 1000
        resident_rows = len(workbench._table.render_session.entries)
        snapshot = services.project_lifecycle.active.variant.snapshot()
        count, full_heartbeat = run_worker(application, lambda: sum(1 for _ in snapshot.entries))
        full_ready = (perf_counter() - started) * 1000
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        saves = []
        if edits:
            keys = tuple(entry.entry_key for entry in snapshot.entries)
            for amount in (0, 1, min(100, len(keys)), min(10_000, len(keys))):
                if amount == 1:
                    require(services.gui_project_commands.update_entry(keys[0], CONTEXT, translation="Single edit"))
                elif amount:
                    require(
                        services.gui_project_commands.replace_entry_states(
                            {key: (f"Edit {amount}:{index}", 1) for index, key in enumerate(keys[:amount])},
                            CONTEXT,
                        )
                    )
                saves.append(measured_save(application, services, store.path, amount))
        return {
            "first_editable_ms": first_editable,
            "full_scope_ready_ms": full_ready,
            "first_page_rows": first_page,
            "first_logical_rows": logical_rows,
            "active_source_resident_ms": resident_ready,
            "active_source_resident_rows": resident_rows,
            "full_scope_rows": count,
            "heartbeat_ms": max(heartbeat, resident_heartbeat, full_heartbeat),
            "python_peak_bytes": peak,
            "saves": saves,
        }
    finally:
        if tracemalloc.is_tracing():
            tracemalloc.stop()
        workbench.close()
        for worker in (
            workbench._paging.worker,
            workbench._paging._statistics_worker,
            workbench._paging._resident_worker,
        ):
            if worker is not None:
                worker.wait()
        application.processEvents()
        workbench.deleteLater()
        services.close()


def require(result):
    if not result.is_success:
        raise RuntimeError("; ".join(item.message for item in result.diagnostics))
    return result.value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", nargs="+", type=int, default=[1_000, 10_000, 100_000])
    parser.add_argument("--sources", type=int, default=80)
    parser.add_argument("--history", nargs="+", type=int, default=[0, 100, 1_000])
    parser.add_argument("--reloads", type=int, default=5)
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    if min(options.rows) < options.sources or options.sources < 1 or options.reloads < 1 or min(options.history) < 0:
        parser.error("each dataset must cover all sources, with at least one reload and nonnegative history")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    application = QApplication([])
    parent = Path.cwd().resolve()
    scratch = parent / f".benchmark-project-storage-{uuid4().hex}"
    scratch.mkdir()
    records = []
    try:
        for count in options.rows:
            for history in options.history:
                workspace = scratch / f"rows-{count}-tasks-{history}"
                store = ProjectStore(project_path(workspace, PROJECT))
                store.create(
                    PROJECT, "Synthetic translation workbench", VARIANT, "Main", sources(count, options.sources)
                )
                task_history(workspace, history)
                first = measure_open(application, workspace, store, edits=False)
                reloads = [
                    measure_open(application, workspace, store, edits=index == options.reloads - 1)
                    for index in range(options.reloads)
                ]
                records.append({
                    "entries": count,
                    "sources": options.sources,
                    "task_history": history,
                    "process_first": first,
                    "reloads": reloads,
                    "project_bytes": store.path.stat().st_size,
                    "reload_first_editable_median_ms": statistics.median(item["first_editable_ms"] for item in reloads),
                })
    finally:
        if scratch.resolve().parent != parent:
            raise RuntimeError("benchmark staging path escaped the workspace")
        rmtree(scratch)
    report = {
        "platform": platform.platform(),
        "python": sys.version,
        "sqlite": sqlite3.sqlite_version,
        "datasets": records,
    }
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if options.output is None:
        print(output)
    else:
        options.output.write_text(output + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
