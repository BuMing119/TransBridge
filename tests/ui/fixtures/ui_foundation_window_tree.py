"""Reproducible FR24 no-Foundation representative Qt window tree.

This module is intentionally test-only.  It constructs real production views
while replacing persistence/bootstrap boundaries that could read or mutate a
developer's workspace.  The canonical authoritative workload is 10,000 table
rows; callers may request a smaller explicitly fingerprinted smoke workload.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import asdict, dataclass
import gc
import hashlib
import json
import os
import platform
import statistics
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch
import weakref

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FIXTURE_SCHEMA_VERSION = 1
CANONICAL_ENTRY_COUNT = 10_000
FIXED_FONT_FAMILY = "Segoe UI"
FIXED_FONT_POINT_SIZE = 10.0
FIXED_LOGICAL_DPI = 96.0
FIXED_DEVICE_PIXEL_RATIO = 1.0
REPRESENTATIVE_COMPONENTS = (
    "transbridge.ui.main_window.MainWindow",
    "transbridge.ui.shell.start_center.StartCenterWidget",
    "transbridge.ui.workbench.widget.WorkbenchWidget",
    "transbridge.ui.workbench.step2.Step2PreviewWidget",
    "transbridge.ui.guidance.qt.GuidanceBanner",
    "transbridge.ui.paratranz.widget.ParaTranzWidget",
    "transbridge.ui.tools.ai_translator.ai_translator_window.AITranslatorWindow",
    "transbridge.ui.tools.smart_assistant.chat_widget.ChatWidget",
    "transbridge.ui.shell.task_center.TaskCenterPanel",
    "transbridge.ui.shell.command_palette_qt.CommandPaletteDialog",
    "transbridge.ui.operations.plan_dialog.OperationPlanDialog",
    "transbridge.ui.tools.fomod.fomod_panel.FomodPanel",
)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * percentile + 0.999999) - 1))
    return ordered[index]


def _rss_bytes() -> int:
    try:
        import psutil

        return int(psutil.Process().memory_info().rss)
    except (ImportError, OSError):
        return 0


def fixture_fingerprint(*, entry_count: int = CANONICAL_ENTRY_COUNT) -> str:
    """Hash workload inputs only, independent of timings and host metadata."""

    payload = {
        "schema": FIXTURE_SCHEMA_VERSION,
        "components": REPRESENTATIVE_COMPONENTS,
        "entry_count": entry_count,
        "font_family": FIXED_FONT_FAMILY,
        "font_point_size": FIXED_FONT_POINT_SIZE,
        "logical_dpi": FIXED_LOGICAL_DPI,
        "device_pixel_ratio": FIXED_DEVICE_PIXEL_RATIO,
        "destinations": ("start-center", "restored-workbench"),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class UiBenchmarkProfile:
    profile_id: str
    fixture_fingerprint: str
    fixture_schema: int
    entry_count: int
    qt_version: str
    pyqt_version: str
    platform: str
    platform_plugin: str
    font_family: str
    point_size: float
    logical_dpi: float
    device_pixel_ratio: float
    warmup: int
    repetitions: int


@dataclass(slots=True)
class RepresentativeWindowTree:
    profile: UiBenchmarkProfile
    main_window: object
    auxiliary_windows: tuple[object, ...]
    patch_stack: ExitStack

    def show(self) -> None:
        from PyQt6.QtWidgets import QApplication

        self.main_window.show()
        for window in self.auxiliary_windows:
            window.show()
        preview = self.main_window.workbench.preview
        deadline = time.monotonic() + 30.0
        while preview._table.rowCount() < self.profile.entry_count and time.monotonic() < deadline:
            QApplication.processEvents()
        if preview._table.rowCount() != self.profile.entry_count:
            raise RuntimeError(
                f"representative table render timed out at {preview._table.rowCount()}/{self.profile.entry_count} rows"
            )
        for _ in range(10):
            QApplication.processEvents()

    def close(self) -> None:
        from PyQt6.QtWidgets import QApplication

        for window in reversed(self.auxiliary_windows):
            shutdown = getattr(window, "shutdown", None)
            if callable(shutdown):
                shutdown(wait_for_worker=False)
            close_runtime = getattr(window, "close_runtime", None)
            if callable(close_runtime):
                close_runtime()
            window.close()
            window.deleteLater()
        lifecycle = getattr(self.main_window, "_window_lifecycle", None)
        if lifecycle is not None:
            lifecycle.auto_saver.stop()
            lifecycle._close_ready = True
        status = getattr(self.main_window, "_status_presenter", None)
        if status is not None:
            status.close()
        intent_composition = getattr(self.main_window, "_intent_composition", None)
        if intent_composition is not None:
            intent_composition.close()
        self.main_window.close_ready = True
        self.main_window.close()
        self.main_window.deleteLater()
        for _ in range(3):
            QApplication.processEvents()
        self.patch_stack.close()


def _profile(app, *, entry_count: int, warmup: int, repetitions: int) -> UiBenchmarkProfile:
    from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
    from PyQt6.QtGui import QGuiApplication

    screen = QGuiApplication.primaryScreen()
    plugin = QGuiApplication.platformName()
    return UiBenchmarkProfile(
        profile_id="fr24-no-foundation-v1",
        fixture_fingerprint=fixture_fingerprint(entry_count=entry_count),
        fixture_schema=FIXTURE_SCHEMA_VERSION,
        entry_count=entry_count,
        qt_version=QT_VERSION_STR,
        pyqt_version=PYQT_VERSION_STR,
        platform=platform.platform(),
        platform_plugin=plugin,
        font_family=app.font().family(),
        point_size=app.font().pointSizeF(),
        logical_dpi=0.0 if screen is None else float(screen.logicalDotsPerInch()),
        device_pixel_ratio=0.0 if screen is None else float(screen.devicePixelRatio()),
        warmup=warmup,
        repetitions=repetitions,
    )


def build_representative_window_tree(
    *, entry_count: int = CANONICAL_ENTRY_COUNT, warmup: int = 2, repetitions: int = 20
) -> RepresentativeWindowTree:
    """Build current production views without network, workspace, or config writes."""

    from PyQt6.QtGui import QFont
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    app.setFont(QFont(FIXED_FONT_FAMILY, int(FIXED_FONT_POINT_SIZE)))

    stack = ExitStack()
    stack.enter_context(
        patch(
            "transbridge.paratranz.config_manager.ParatranzConfig.create_or_load",
            return_value=SimpleNamespace(token="", user_id=None),
        )
    )
    stack.enter_context(
        patch("transbridge.ui.shell.window_lifecycle.WindowLifecycle.restore_state", lambda _self: None)
    )
    stack.enter_context(patch("transbridge.ui.shell.window_lifecycle.WindowLifecycle.start", lambda _self: None))
    stack.enter_context(
        patch("transbridge.ui.coordinators.project_coordinator.ProjectCoordinator.init_workspace", lambda _self: None)
    )

    try:
        from transbridge.config.llm import LLMConfig
        from transbridge.converter.translation_entry import TranslationEntry
        from transbridge.converter.translation_entry_collection import TranslationEntryCollection
        from transbridge.ui.main_window import MainWindow
        from transbridge.ui.operations.plan_dialog import OperationPlanDialog
        from transbridge.ui.operations.plan_view import OperationKind, OperationPlanViewState
        from transbridge.ui.projection_types import CollectionSlot
        from transbridge.ui.shell.action_catalog import DEFAULT_ACTION_CATALOG
        from transbridge.ui.shell.command_palette import CommandPaletteController, CommandPaletteModel
        from transbridge.ui.shell.command_palette_qt import CommandPaletteDialog
        from transbridge.ui.shell.task_center import TaskCenterPanel
        from transbridge.ui.tools.ai_translator import config_presenter as ai_config_module
        from transbridge.ui.tools.ai_translator.ai_translator_window import AITranslatorWindow
        from transbridge.ui.tools.fomod.fomod_panel import FomodPanel
        from transbridge.ui.tools.smart_assistant.chat_widget import ChatWidget

        stack.enter_context(patch.object(ai_config_module.LLMConfig, "load_from_file", return_value=LLMConfig()))
        main_window = MainWindow()
        collection = TranslationEntryCollection(
            TranslationEntry(str(index), f"key-{index:05d}", f"Original {index}", "", index % 4, "NPC_:FULL")
            for index in range(entry_count)
        )
        main_window.context.add_slot(
            "fixture://representative.esp",
            CollectionSlot("Representative", collection, esp_path="fixture://representative.esp"),
        )
        main_window.workbench.preview.refresh(collection)

        ai_window = AITranslatorWindow(main_window.context, main_window.workbench.preview, main_window)
        chat = ChatWidget(main_window.context, main_window)
        task_center = TaskCenterPanel(main_window)
        availability = tuple(
            DEFAULT_ACTION_CATALOG.availability(item.intent_id, enabled=True) for item in DEFAULT_ACTION_CATALOG.all()
        )
        palette = CommandPaletteDialog(CommandPaletteController(CommandPaletteModel(lambda: availability)), main_window)
        palette.open_palette()
        operation = OperationPlanDialog(
            OperationPlanViewState(
                session_id="fixture-operation",
                revision=1,
                kind=OperationKind.WRITE,
                title="代表性写入计划",
                target="fixture://output",
                scope_summary=f"{entry_count} 条词条",
                mode_summary="基线",
                conflict_summary="无",
                backup_summary="启用",
                estimated_impact=(("entries", entry_count),),
                request_digest="0" * 64,
            ),
            main_window,
        )
        fomod = FomodPanel(main_window.context, main_window)
        auxiliary = (ai_window, chat, task_center, palette, operation, fomod)
        return RepresentativeWindowTree(
            profile=_profile(app, entry_count=entry_count, warmup=warmup, repetitions=repetitions),
            main_window=main_window,
            auxiliary_windows=auxiliary,
            patch_stack=stack.pop_all(),
        )
    except Exception:
        stack.close()
        raise


def measure_baseline(*, entry_count: int, repetitions: int, lifecycle_iterations: int) -> dict[str, object]:
    """Measure the current no-Foundation tree; this is not a final theme pass."""

    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    app_started = time.perf_counter()
    app = QApplication.instance() or QApplication([])
    qapplication_create_s = time.perf_counter() - app_started
    rss_before = _rss_bytes()
    placeholder_started = time.perf_counter()
    foundation_present = os.path.isdir(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "src", "transbridge", "ui", "foundation")
    )
    foundation_placeholder_s = time.perf_counter() - placeholder_started

    samples: list[float] = []
    heartbeat_gaps: list[float] = []
    profile = None
    for _ in range(repetitions):
        ticks = [time.perf_counter()]
        heartbeat = QTimer()
        heartbeat.setInterval(10)
        heartbeat.timeout.connect(lambda: ticks.append(time.perf_counter()))
        heartbeat.start()
        started = time.perf_counter()
        tree = build_representative_window_tree(entry_count=entry_count, repetitions=repetitions)
        tree.show()
        samples.append(time.perf_counter() - started)
        profile = tree.profile
        ticks.append(time.perf_counter())
        heartbeat.stop()
        heartbeat_gaps.append(max((b - a for a, b in zip(ticks, ticks[1:])), default=0.0))
        tree.close()

    gc.collect()
    app.processEvents()
    gc.collect()
    control_tree = build_representative_window_tree(entry_count=entry_count, repetitions=repetitions)
    control_tree.show()
    for _ in range(10):
        app.processEvents()
    gc.collect()
    app.processEvents()
    gc.collect()
    rss_warmed = _rss_bytes()

    class _NoopRevision:
        pass

    references: list[weakref.ReferenceType] = []
    noop_samples: list[float] = []
    for _ in range(lifecycle_iterations):
        started = time.perf_counter()
        marker = _NoopRevision()
        marker.revision = 0
        references.append(weakref.ref(marker))
        del marker
        app.processEvents()
        noop_samples.append(time.perf_counter() - started)
    gc.collect()
    app.processEvents()
    rss_after = _rss_bytes()
    control_tree.close()
    assert profile is not None
    return {
        "schema_version": 1,
        "mode": "baseline-no-foundation",
        "authority": "windows-visible"
        if sys.platform == "win32" and app.platformName() == "windows"
        else "partial-boundary",
        "profile": asdict(profile),
        "environment": {
            "python": platform.python_version(),
            "executable": sys.executable,
            "rss_proxy": "psutil" if rss_before else "unavailable",
        },
        "cold_boundary": {
            "qapplication_create_s": qapplication_create_s,
            "foundation_present": foundation_present,
            "foundation_applied": False,
            "foundation_placeholder_s": foundation_placeholder_s,
            "rss_before_bytes": rss_before,
            "rss_after_bytes": rss_after,
            "rss_peak_bytes": max(rss_before, rss_warmed, rss_after),
        },
        "window_open": {
            "samples_s": samples,
            "p50_s": statistics.median(samples),
            "p95_s": _percentile(samples, 0.95),
        },
        "heartbeat_max_s": max(heartbeat_gaps, default=0.0),
        "noop_switch_control": {
            "iterations": lifecycle_iterations,
            "p95_s": _percentile(noop_samples, 0.95),
            "live_python_markers": sum(reference() is not None for reference in references),
            "rss_warmed_bytes": rss_warmed,
            "rss_after_bytes": rss_after,
            "rss_delta_bytes": rss_after - rss_warmed if rss_warmed and rss_after else 0,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entries", type=int, default=1_200)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--lifecycle-iterations", type=int, default=100)
    args = parser.parse_args(argv)
    if args.entries < 1 or args.repetitions < 1 or args.lifecycle_iterations < 1:
        parser.error("entries, repetitions and lifecycle iterations must be positive")
    print(
        json.dumps(
            measure_baseline(
                entry_count=args.entries,
                repetitions=args.repetitions,
                lifecycle_iterations=args.lifecycle_iterations,
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
