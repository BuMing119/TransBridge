"""Measure the real Qt project-open coordinator on isolated persistence copies.

Example: uv run python scripts/benchmark_project_open.py --output result.json
Use --source-root for another checkout (only src/ is needed). Real projects are
opt-in with --data-root; only project/variant records are copied. Their source
files are read by the normal parser, and input hashes are checked afterwards.
No application configuration, credentials, network or LLM services are loaded.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from functools import wraps
import gc
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import re
import statistics
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
from unittest.mock import patch
import uuid


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def copy_records(source: Path, destination: Path) -> tuple[list[Path], dict[Path, str]]:
    """Copy only records, without constructing any service against user data."""
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("copy destination must be outside the source data tree")
    if destination.exists():
        raise FileExistsError("copy destination must be new")
    records = sorted((source / "projects").glob("*.json"))
    variants = sorted((source / "projects").glob("*/variants/*.json"))
    if not records:
        raise ValueError("source data contains no project records")
    hashes = {}
    for path in [*records, *variants]:
        if path.is_symlink() or not path.resolve().is_relative_to(source):
            raise ValueError("record links outside the source tree are unsupported")
        hashes[path] = _digest(path)
    # Track source content too, without including its text or filenames in JSON.
    for record in records:
        document = json.loads(record.read_text(encoding="utf-8"))
        for item in document.get("data", {}).get("sources", ()):
            location = item.get("location") or item.get("path")
            if location and Path(location).is_file():
                path = Path(location).resolve()
                hashes[path] = _digest(path)
    destination.mkdir()
    for path in [*records, *variants]:
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
        if _digest(target) != hashes[path]:
            raise RuntimeError("input changed while copying project records")
    return [destination / path.relative_to(source) for path in records], hashes


def verify_inputs(hashes: dict[Path, str]) -> bool:
    return all(path.is_file() and _digest(path) == digest for path, digest in hashes.items())


def _services(root: Path):
    from transbridge.bootstrap.persistence import build_persistence_v2_services

    return build_persistence_v2_services(
        root,
        id_factory=lambda: uuid.uuid4().hex,
        timestamp_factory=lambda: "2026-10-19T00:00:00+00:00",
    )


def create_fixture(root: Path, count: int) -> Path:
    """Build synthetic records with the same source parser used when opening."""
    from transbridge.application.contracts import RequestContext
    from transbridge.application.io import FormatId
    from transbridge.application.projects import ProjectSourceRequest
    from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer
    from transbridge.persistence.v2 import (
        ProjectDto,
        ProjectId,
        ProjectRef,
        SchemaEnvelope,
        VariantId,
        VariantRef,
        VariantSnapshot,
    )

    if count < 1:
        raise ValueError("entry count must be positive")
    root.mkdir(parents=True, exist_ok=False)
    source = root / "synthetic-source.json"
    source.write_text(
        json.dumps([
            {
                "key": f"entry-{index:06d}",
                "original": f"Synthetic original {index}",
                "translation": f"Synthetic translation {index}",
                "stage": 1,
                "context": "NPC_:FULL",
            }
            for index in range(count)
        ]),
        encoding="utf-8",
    )
    prepared = TranslationIoProjectSourcePreparer().prepare_source(
        ProjectSourceRequest(str(source), format_hint=FormatId.JSON_PARATRANZ),
        RequestContext("benchmark", run_id="fixture"),
        role="primary",
        common_options=(),
    )
    services = _services(root)
    try:
        project = ProjectRef(ProjectId("benchmark-project"))
        variant = VariantRef(VariantId("benchmark-variant"), project.identity)
        services.projects.save(
            project,
            ProjectDto(
                SchemaEnvelope(
                    2,
                    project.kind,
                    project.identity.value,
                    0,
                    {
                        "name": "Synthetic benchmark",
                        "sources": [prepared.to_dict()],
                        "variant_ids": [variant.identity.value],
                        "active_variant_id": variant.identity.value,
                    },
                )
            ),
        )
        services.variants.save(
            variant,
            VariantSnapshot(variant, (prepared.baseline.fingerprint,), prepared.baseline.entries, 0).to_dto(),
        )
        return Path(services.projects.path_for(project))
    finally:
        services.close()


class Measurements:
    """Per-run timings; wrappers measure production calls without replacing them."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.milliseconds: dict[str, float] = {}
        self.started = time.perf_counter()
        self.first_rows = None
        self.finished_rows = None
        self.render_generations = 0
        self.max_batch_ms = 0.0
        self.failures: list[str] = []

    def wrap(self, stack, owner, name, metric):
        original = getattr(owner, name)

        @wraps(original)
        def timed(*args, **kwargs):
            started = time.perf_counter()
            try:
                result = original(*args, **kwargs)
                if name in {"prepare_path", "activate"}:
                    if not result.is_success:
                        self.failures.extend(item.code for item in result.diagnostics)
                    elif name == "activate" and result.value and result.value.get("recovery") is not None:
                        self.failures.append("PROJECT_SOURCE_RECOVERY_AVAILABLE")
                return result
            finally:
                elapsed = (time.perf_counter() - started) * 1000
                self.milliseconds[metric] = self.milliseconds.get(metric, 0) + elapsed

        stack.enter_context(patch.object(owner, name, timed))


class RenderObservation:
    """Observe row availability and viewport paint for either table generation."""

    def __init__(self, table, measurements, stack):
        from PyQt6.QtCore import QEvent, QObject
        from PyQt6.QtWidgets import QTableWidget

        self.table, self.measurements = table, measurements
        self.mode = "item_batches" if isinstance(table, QTableWidget) else "virtual_model"
        self.reset()
        owner = self

        class PaintObserver(QObject):
            def eventFilter(self, _watched, event):  # noqa: N802 - Qt override
                if event.type() == QEvent.Type.Paint and measurements.render_generations and owner.row_count():
                    owner.paint_observed = True
                return False

        self.paint_observer = PaintObserver(table.viewport())
        table.viewport().installEventFilter(self.paint_observer)
        original_start = table.start_render

        @wraps(original_start)
        def start_render(*args, **kwargs):
            measurements.render_generations += 1
            result = original_start(*args, **kwargs)
            self.record_rows()
            return result

        stack.enter_context(patch.object(table, "start_render", start_render))
        measurements.wrap(stack, table, "start_render", "table_start_render_ms")
        if self.mode == "item_batches":
            original_batch = table.append_batch

            @wraps(original_batch)
            def append_batch(*args, **kwargs):
                started = time.perf_counter()
                result = original_batch(*args, **kwargs)
                measurements.max_batch_ms = max(measurements.max_batch_ms, (time.perf_counter() - started) * 1000)
                self.record_rows()
                return result

            stack.enter_context(patch.object(table, "append_batch", append_batch))

    def reset(self):
        self.paint_observed = False
        self.first_viewport_paint_ms = None

    def row_count(self):
        return self.table.model().rowCount()

    def record_rows(self):
        elapsed = (time.perf_counter() - self.measurements.started) * 1000
        rows = self.row_count()
        if rows and self.measurements.first_rows is None:
            self.measurements.first_rows = elapsed
        if rows == len(self.table.render_session.entries) and self.measurements.finished_rows is None:
            self.measurements.finished_rows = elapsed

    def process_events_completed(self):
        # eventFilter runs before paint. Timestamp only after Qt dispatch returns.
        if self.paint_observed and self.first_viewport_paint_ms is None:
            self.first_viewport_paint_ms = (time.perf_counter() - self.measurements.started) * 1000


def _make_host(services, measurements, stack):
    from PyQt6.QtWidgets import QWidget

    from transbridge.application.contracts import RequestContext
    from transbridge.ui.context import AppContext
    from transbridge.ui.coordinators.project_coordinator import ProjectCoordinator
    from transbridge.ui.workbench.step2 import Step2PreviewWidget

    # AppContext normally creates/loads user settings. This host has no account.
    stack.enter_context(
        patch(
            "transbridge.ui.context.ParatranzConfig.create_or_load",
            return_value=SimpleNamespace(user_id=None, base_url="", api_token=""),
        )
    )
    context = AppContext(
        project_projection=services.project_projection,
        project_commands=services.gui_project_commands,
        runtime_context=RequestContext("gui", run_id="benchmark"),
    )
    preview = Step2PreviewWidget(context)
    preview.resize(1280, 800)
    preview.show()

    class Host(QWidget):
        def __init__(self):
            super().__init__()
            self.context = context
            self.runtime_context = RequestContext("gui", run_id="benchmark")
            self.current_project_opener = services.current_project_opener
            self.app_runtime = SimpleNamespace(use_cases=SimpleNamespace(resolve=self.resolve))
            self.project_open_worker = self.save_worker = self.foreground_worker = None
            self.workers = []
            self.workbench = SimpleNamespace(
                preview=preview,
                show_step2_progress=preview.show_progress,
                hide_step2_progress=preview.hide_progress,
            )
            self.opened = False

        def resolve(self, name):
            return getattr(services, name)

        def show_message(self, message):
            code = message.partition(":")[0]
            if re.fullmatch(r"[A-Z][A-Z0-9_]+", code):
                measurements.failures.append(code)

        def show_workbench(self):
            self.opened = True

        def show_project_open_progress(self, _message):
            pass

        def hide_project_open_progress(self):
            pass

    host = Host()
    coordinator = ProjectCoordinator(host)
    measurements.wrap(stack, host.current_project_opener, "prepare_path", "prepare_ms")
    measurements.wrap(stack, host.current_project_opener, "activate", "activation_ms")
    measurements.wrap(stack, coordinator, "_restore_plugin_sources", "gui_hydration_install_ms")
    if hasattr(context, "install_project_open"):
        measurements.wrap(stack, context, "install_project_open", "gui_hydration_install_ms")
    import transbridge.ui.source_hydration as source_hydration

    if hasattr(source_hydration, "prepare_project_slots"):
        from transbridge.ui.coordinators import project_open_operation as operation

        original_prepare = source_hydration.prepare_project_slots
        measurements.wrap(stack, source_hydration, "prepare_project_slots", "prepare_view_ms")
        if getattr(operation, "prepare_project_slots", None) is original_prepare:
            measurements.wrap(stack, operation, "prepare_project_slots", "prepare_view_ms")
        if hasattr(operation, "prepare_project_view"):
            measurements.wrap(stack, operation, "prepare_project_view", "prepare_view_state_ms")
    host.render_observation = RenderObservation(preview._table, measurements, stack)

    def warning(_parent, _title, message, *args, **kwargs):
        # Keep diagnostics private: messages can contain source text/paths.
        if not measurements.failures:
            measurements.failures.append("PROJECT_OPEN_FAILED")
        return 0

    stack.enter_context(patch("PyQt6.QtWidgets.QMessageBox.warning", warning))
    stack.enter_context(
        patch.object(sys, "excepthook", lambda typ, _value, _tb: measurements.failures.append(typ.__name__))
    )
    return host, coordinator, preview, context


def measure_project(app, root: Path, project: Path, repeats: int, timeout: float) -> dict:
    from PyQt6.QtCore import QTimer

    services = _services(root)
    measurements = Measurements()
    samples = []
    with ExitStack() as stack:
        host, coordinator, preview, context = _make_host(services, measurements, stack)
        try:
            for iteration in range(repeats + 1):
                gc.collect()
                app.processEvents()
                measurements.reset()
                host.render_observation.reset()
                host.opened = False
                ticks = [measurements.started]
                heartbeat = QTimer()
                heartbeat.setInterval(10)
                heartbeat.timeout.connect(lambda: ticks.append(time.perf_counter()))
                heartbeat.start()
                try:
                    coordinator.open_project_path(str(project))
                    while True:
                        app.processEvents()
                        host.render_observation.process_events_completed()
                        if measurements.failures:
                            break
                        rows_painted = (
                            host.render_observation.first_viewport_paint_ms is not None
                            or host.render_observation.row_count() == 0
                        )
                        if (
                            host.opened
                            and host.project_open_worker is None
                            and not preview._table.has_pending_batch
                            and rows_painted
                        ):
                            break
                        if time.perf_counter() - measurements.started > timeout:
                            measurements.failures.append("TIMEOUT")
                            break
                        time.sleep(0.001)
                    # Process a paint and a heartbeat after the final result slot.
                    app.processEvents()
                    host.render_observation.process_events_completed()
                finally:
                    ticks.append(time.perf_counter())
                    heartbeat.stop()
                elapsed = (time.perf_counter() - measurements.started) * 1000
                success = host.opened and not measurements.failures
                samples.append({
                    "phase": "cold_open" if iteration == 0 else "reload",
                    "success": success,
                    "failure_codes": sorted(set(measurements.failures)) or ([] if success else ["OPEN_NOT_COMPLETED"]),
                    "entries": host.render_observation.row_count() if success else None,
                    "table_mode": host.render_observation.mode,
                    "render_generations": measurements.render_generations,
                    "ms": {
                        **measurements.milliseconds,
                        "first_rows_ready_ms": measurements.first_rows,
                        "all_rows_ready_ms": measurements.finished_rows,
                        "first_viewport_paint_processed_ms": host.render_observation.first_viewport_paint_ms,
                        "max_table_batch_ms": measurements.max_batch_ms
                        if host.render_observation.mode == "item_batches"
                        else None,
                        "total_ms": elapsed,
                        "max_event_loop_gap_ms": max(b - a for a, b in zip(ticks, ticks[1:])) * 1000,
                    },
                })
                if not success:
                    break
        finally:
            # Never destroy a running QThread or its data after a timeout/error.
            while any(worker.isRunning() for worker in host.workers):
                app.processEvents()
                time.sleep(0.005)
            app.processEvents()
            preview.close()
            context.close_projection()
            host.close()
            services.close()
            preview.deleteLater()
            context.deleteLater()
            host.deleteLater()
            app.processEvents()
    reloads = [sample for sample in samples if sample["success"] and sample["phase"] == "reload"]
    metrics = {}
    if reloads:
        for key in reloads[0]["ms"]:
            values = [sample["ms"][key] for sample in reloads if sample["ms"].get(key) is not None]
            if values:
                metrics[key] = {"median": statistics.median(values), "min": min(values), "max": max(values)}
    return {
        "completed": len(reloads) == repeats,
        "successful_reloads": len(reloads),
        "samples": samples,
        "reload_metrics_ms": metrics,
    }


def _arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--data-root", type=Path, help="Opt-in real records; always copied before services are built")
    parser.add_argument("--entries", nargs="+", type=int, default=[1000, 10000])
    parser.add_argument("--case", nargs="+", type=int, help="Measure only these one-based case indices")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--output", type=Path, required=True, help="New JSON path, outside the input data root")
    args = parser.parse_args(argv)
    if args.repeats < 5 or min(args.entries) < 1 or args.timeout <= 0:
        parser.error("at least five repetitions, positive entry counts and timeout are required")
    if args.case and (min(args.case) < 1 or len(set(args.case)) != len(args.case)):
        parser.error("case indices must be positive and unique")
    if args.output.exists() or args.output.is_symlink():
        parser.error("output must be a new path")
    if not (args.source_root / "src/transbridge").is_dir():
        parser.error("source-root must contain src/transbridge")
    if args.data_root and args.output.resolve().is_relative_to(args.data_root.resolve()):
        parser.error("output must be outside the input data root")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    source = (args.source_root / "src").resolve()
    loaded = sys.modules.get("transbridge")
    if loaded is not None and not Path(loaded.__file__).resolve().is_relative_to(source):
        raise RuntimeError("source-root must be selected in a fresh Python process")
    sys.path.insert(0, str(source))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # Normal parsers can log file names or content; the report uses only metrics.
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        return _execute(args)
    finally:
        logging.disable(previous_logging)
        sys.path.remove(str(source))


def _execute(args) -> int:
    from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    report = {
        "schema_version": 2,
        "completed": False,
        "python": sys.version,
        "os": platform.platform(),
        "qt": QT_VERSION_STR,
        "pyqt": PYQT_VERSION_STR,
        "qt_platform": app.platformName(),
        "font": app.font().toString(),
        "logical_dpi": app.primaryScreen().logicalDotsPerInch(),
        "device_pixel_ratio": app.primaryScreen().devicePixelRatio(),
        "source_root": str(args.source_root.resolve()),
        "repetitions": args.repeats,
        "synthetic": args.data_root is None,
        "selected_cases": args.case,
        "scope": "ProjectCoordinator + AppContext + Step2PreviewWidget; excludes full MainWindow subscriptions",
        "timing": "One cold open then measured reloads; setup/cleanup excluded. 10ms QTimer includes scheduling/GIL.",
        "render_timing": (
            "Row-ready means data accessible to Qt, not all rows painted. Virtual models expose all rows at reset; "
            "legacy item tables expose batches. Viewport paint is timed after dispatch of an observed paint event, "
            "not monitor presentation. Batch timing is null for virtual models."
        ),
        "source_inputs_unchanged": None,
        "results": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents races overwriting a report or a user file.
    with args.output.open("x", encoding="utf-8") as output:
        hashes = {}
        try:
            with TemporaryDirectory(prefix="transbridge-project-open-") as temporary:
                root = Path(temporary)
                if args.data_root:
                    copied = root / "real"
                    projects, hashes = copy_records(args.data_root, copied)
                    cases = [(copied, project) for project in projects]
                else:
                    cases = [
                        (root / f"synthetic-{count}", create_fixture(root / f"synthetic-{count}", count))
                        for count in args.entries
                    ]
                selected_cases = args.case or range(1, len(cases) + 1)
                if any(index > len(cases) for index in selected_cases):
                    raise ValueError("selected case does not exist")
                for index in selected_cases:
                    data_root, project = cases[index - 1]
                    result = measure_project(app, data_root, project, args.repeats, args.timeout)
                    report["results"].append({"case": index, **result})
                report["completed"] = all(item["completed"] for item in report["results"])
        except Exception as exc:  # Keep source paths/text and credentials out of results.
            report["failure_type"] = type(exc).__name__
        finally:
            report["source_inputs_unchanged"] = verify_inputs(hashes) if hashes else None
            if report["source_inputs_unchanged"] is False:
                report["completed"] = False
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
    print(json.dumps({"completed": report["completed"], "cases": len(report["results"])}))
    return 0 if report["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
