"""Run the same synthetic open/save scenario against either project storage implementation.

Invoke twice with the same interpreter and arguments, changing only --source-root
and --output. A source root needs only src/transbridge, e.g. git archive HEAD src.
Each run owns a disposable data directory and imports no real projects or settings.
The first measured open is process-first after setup; OS caches are not flushed.
Save elapsed time measures persistence; edit_prepare_ms times the synchronous
edit command and edit_and_save_ms sums both stages.
"""

from __future__ import annotations

import argparse
import gc
import json
import logging
import os
from pathlib import Path
import platform
from shutil import rmtree
import statistics
import sys
from time import perf_counter, sleep
from uuid import uuid4

from benchmark_project_open import Measurements, _make_host, _services


def fixture(root, count, source_count, context):
    from transbridge.application.io import FormatId
    from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest

    input_dir = root / "input"
    input_dir.mkdir()
    sources = []
    start = 0
    for source_index in range(source_count):
        path = input_dir / f"source-{source_index:03d}.json"
        stop = start + count // source_count + int(source_index < count % source_count)
        path.write_text(
            json.dumps([
                {
                    "id": index + 1,
                    "key": f"entry-{index:06d}",
                    "original": f"Synthetic original {index}",
                    "translation": "" if index % 5 == 0 else f"Synthetic translation {index}",
                    "stage": 0 if index % 5 == 0 else 1,
                    "context": "NPC_:FULL|" + "context " * (index % 20),
                }
                for index in range(start, stop)
            ]),
            encoding="utf-8",
        )
        sources.append(ProjectSourceRequest(str(path), format_hint=FormatId.JSON_PARATRANZ))
        start = stop
    data_root = root / "workspace"
    services = _services(data_root)
    try:
        require(
            services.gui_project_commands.create_project(
                ProjectProvisioningRequest(
                    "Synthetic benchmark", source=sources[0], additional_sources=tuple(sources[1:])
                ),
                context,
            )
        )
        return data_root, Path(services.projects.path_for(services.project_lifecycle.active.project_ref))
    finally:
        services.close()


def require(result):
    if not result.is_success:
        raise RuntimeError("; ".join(f"{item.code}: {item.message}" for item in result.diagnostics))
    return result.value


def observe(app, process, start, done, timeout):
    from PyQt6.QtCore import QTimer

    began = perf_counter()
    ticks = [began]
    memory = [process.memory_info().rss]
    timer = QTimer()
    timer.setInterval(10)

    def sample():
        ticks.append(perf_counter())
        memory.append(process.memory_info().rss)

    timer.timeout.connect(sample)
    timer.start()
    try:
        start()
        while True:
            app.processEvents()
            if done():
                break
            if perf_counter() - began > timeout:
                raise TimeoutError("benchmark operation did not finish")
            sleep(0.001)
        elapsed = (perf_counter() - began) * 1000
    finally:
        timer.stop()
        sample()
    return {
        "elapsed_ms": elapsed,
        "max_heartbeat_ms": max((end - start) * 1000 for start, end in zip(ticks, ticks[1:], strict=False)),
        "sampled_rss_peak_bytes": max(memory),
    }


def worker_observation(app, process, operation, timeout):
    from transbridge.ui.workers import ApiWorker

    results, errors = [], []
    worker = ApiWorker(operation, route_http_errors=False)
    worker.result.connect(results.append)
    worker.error.connect(errors.append)
    try:
        measurement = observe(
            app, process, worker.start, lambda: not worker.isRunning() and bool(results or errors), timeout
        )
        if errors:
            raise RuntimeError(errors[0])
        return results[0], measurement
    finally:
        worker.wait()
        worker.deleteLater()
        app.processEvents()


def storage_bytes(root):
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def measure(app, process, root, project, context, args):
    from contextlib import ExitStack

    services = _services(root)
    measurements = Measurements()
    samples = []
    with ExitStack() as stack:
        host, coordinator, preview, app_context = _make_host(services, measurements, stack)
        render = host.render_observation
        try:
            for iteration in range(args.repeats + 1):
                gc.collect()
                app.processEvents()
                measurements.reset()
                render.reset()
                host.opened = False

                def opened():
                    render.process_events_completed()
                    if measurements.failures:
                        raise RuntimeError(f"opening failed: {measurements.failures}")
                    return (
                        host.opened
                        and host.project_open_worker is None
                        and not preview._table.has_pending_batch
                        and render.first_viewport_paint_ms is not None
                    )

                opening = observe(
                    app, process, lambda: coordinator.open_project_path(str(project)), opened, args.timeout
                )
                opening.update({
                    "first_rows_ms": measurements.first_rows,
                    "first_viewport_paint_ms": render.first_viewport_paint_ms,
                    "table_rows": render.row_count(),
                    "materialized_table_rows": render.materialized_rows(),
                    "open_stage_ms": measurements.milliseconds,
                })
                paging = getattr(preview, "_paging", None)
                if paging is not None and hasattr(paging, "resident_ready"):
                    resident = observe(app, process, lambda: None, lambda: paging.resident_ready, args.timeout)
                    resident["ready_from_open_ms"] = (perf_counter() - measurements.started) * 1000
                    resident["rows"] = render.materialized_rows()
                    opening["resident_source"] = resident
                active = services.project_lifecycle.active

                def full_scope():
                    return {entry.entry_key.local_key: entry.entry_key for entry in active.variant.snapshot().entries}

                keys, scope = worker_observation(app, process, full_scope, args.timeout)
                if len(keys) != args.rows:
                    raise RuntimeError(f"complete scope returned {len(keys)} entries, expected {args.rows}")
                scope["rows"] = len(keys)
                scope["ready_from_open_ms"] = (perf_counter() - measurements.started) * 1000
                saves = []
                if iteration:
                    for amount in dict.fromkeys((0, 1, min(100, args.rows), min(10_000, args.rows))):
                        edit_prepare_ms = 0.0
                        if amount:
                            changes = {
                                keys[f"entry-{index:06d}"]: (f"Edit {iteration}:{amount}:{index}", 1)
                                for index in range(amount)
                            }
                            started = perf_counter()
                            require(services.gui_project_commands.replace_entry_states(changes, context))
                            edit_prepare_ms = (perf_counter() - started) * 1000
                        before = process.io_counters().write_bytes
                        result, save = worker_observation(
                            app, process, lambda: services.gui_project_commands.save(context), args.timeout
                        )
                        require(result)
                        save.update(
                            edits=amount,
                            edit_prepare_ms=edit_prepare_ms,
                            edit_and_save_ms=edit_prepare_ms + save["elapsed_ms"],
                            process_write_bytes=process.io_counters().write_bytes - before,
                        )
                        saves.append(save)
                samples.append({"open": opening, "full_scope": scope, "saves": saves})
        finally:
            while any(worker.isRunning() for worker in host.workers):
                app.processEvents()
                sleep(0.005)
            preview.close()
            paging = getattr(preview, "_paging", None)
            if paging is not None:
                for worker in (paging.worker, paging._statistics_worker, getattr(paging, "_resident_worker", None)):
                    if worker is not None:
                        worker.wait()
            app.processEvents()
            app_context.close_projection()
            host.close()
            services.close()
    return samples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--sources", type=int, default=80)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    source = (args.source_root / "src").resolve(strict=True)
    if not (source / "transbridge").is_dir() or not (1 <= args.sources <= args.rows) or args.repeats < 1:
        parser.error("supply a source snapshot, 1 <= sources <= rows, and at least one reload")
    if args.timeout <= 0 or args.output.exists() or args.output.is_symlink():
        parser.error("supply a positive timeout and a fresh output path")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    logging.disable(logging.CRITICAL)

    import psutil
    from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
    from PyQt6.QtWidgets import QApplication

    from transbridge.application.contracts import RequestContext

    context = RequestContext("gui", run_id="compare-project-storage")
    app = QApplication([])
    process = psutil.Process()
    parent = Path.cwd().resolve()
    directory = parent / f".tmp-fr32-storage-comparison-{uuid4().hex}"
    directory.mkdir()
    try:
        workspace, project = fixture(directory, args.rows, args.sources, context)
        gc.collect()
        initial_rss = process.memory_info().rss
        initial_bytes = storage_bytes(workspace)
        samples = measure(app, process, workspace, project, context, args)
        report = {
            "environment": {
                "os": platform.platform(),
                "python": sys.version,
                "qt": QT_VERSION_STR,
                "pyqt": PYQT_VERSION_STR,
                "qt_platform": app.platformName(),
            },
            "fixture": {"name": "storage-comparison-paratranz-v1", "rows": args.rows, "sources": args.sources},
            "rss_before_open_bytes": initial_rss,
            "workspace_bytes_before_open": initial_bytes,
            "workspace_bytes_after_saves": storage_bytes(workspace),
            "process_first": samples[0],
            "reloads": samples[1:],
            "reload_first_paint_median_ms": statistics.median(
                sample["open"]["first_viewport_paint_ms"] for sample in samples[1:]
            ),
        }
    finally:
        if directory.resolve().parent != parent:
            raise RuntimeError("benchmark staging path escaped the workspace")
        rmtree(directory)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
