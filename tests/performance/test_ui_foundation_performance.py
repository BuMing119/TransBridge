from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from . import benchmark_cases as cases

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "tests" / "ui" / "fixtures" / "ui_foundation_window_tree.py"
AUDIT_PATH = REPO_ROOT / "scripts" / "audit_ui_foundation.py"


_FINAL_FOUNDATION_PROFILE = r"""
from __future__ import annotations

import gc
import importlib.util
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication

from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.config.repository import ConfigRepository
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode, UiPreferenceRepository
from transbridge.ui.foundation.icons import IconProvider
from transbridge.ui.foundation.runtime import GuiFoundation
from transbridge.ui.foundation.theme_service import ThemePreference


def percentile(values, fraction):
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * fraction + 0.999999) - 1))
    return ordered[index]


def rss_bytes():
    try:
        import psutil
        return int(psutil.Process().memory_info().rss)
    except (ImportError, OSError):
        return 0


def load_fixture(path):
    spec = importlib.util.spec_from_file_location("fr24_final_fixture", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fixture = load_fixture(Path(os.environ["FR24_FIXTURE_PATH"]))
authoritative_requested = os.environ.get("TRANSBRIDGE_UI_FOUNDATION_AUTHORITATIVE") == "1"
app = QApplication.instance() or QApplication([])
visible_windows = sys.platform == "win32" and app.platformName() == "windows"
authority = "windows-visible" if authoritative_requested and visible_windows else "partial-boundary"
entries = fixture.CANONICAL_ENTRY_COUNT if authority == "windows-visible" else 48
# Keep the contract's full sampling depth even for the reduced offscreen tree.
# A two-sample nearest-rank P95 is merely the noisier maximum and can invert a
# relative before/after comparison; only the workload size changes on dev.
repetitions = 20
warmup = 2
iterations = int(os.environ["FR24_LIFECYCLE_ITERATIONS"])
original_palette = QPalette(app.palette())

with tempfile.TemporaryDirectory(prefix="fr24-foundation-") as directory:
    config_path = Path(directory) / "transbridge.ini"
    preferences = UiPreferenceRepository(
        ConfigRepository(
            config_path,
            legacy_path=config_path,
            credential_store=UnavailableCredentialStore(),
        )
    )

    cold_samples = []
    cold_rss_samples = []
    foundation = None
    for index in range(repetitions):
        sample_path = Path(directory) / f"cold-{index}.ini"
        sample_preferences = UiPreferenceRepository(
            ConfigRepository(
                sample_path,
                legacy_path=sample_path,
                credential_store=UnavailableCredentialStore(),
            )
        )
        before_rss = rss_bytes()
        started = time.perf_counter()
        candidate = GuiFoundation.create(app, sample_preferences)
        cold_samples.append(time.perf_counter() - started)
        after_rss = rss_bytes()
        cold_rss_samples.append(max(0, after_rss - before_rss) if before_rss and after_rss else 0)
        candidate.close()

    def window_sample(factory_patch=None):
        # Collect deferred QObject/Python garbage outside the measured region.
        # Automatic cyclic GC is then paused only around construct/show so a
        # random collection cannot dominate one side of the relative P95.
        app.processEvents()
        gc.collect()
        app.processEvents()
        context = factory_patch() if factory_patch is not None else None
        if context is not None:
            context.start()
        tree = None
        gc_was_enabled = gc.isenabled()
        if gc_was_enabled:
            gc.disable()
        try:
            started = time.perf_counter()
            tree = fixture.build_representative_window_tree(
                entry_count=entries,
                warmup=warmup,
                repetitions=repetitions,
            )
            constructed = time.perf_counter()
            tree.show()
            shown = time.perf_counter()
            theme_view = getattr(tree.main_window, "theme_view", None)
            live_count = 0 if theme_view is None else theme_view.active_subscription_count
        finally:
            if gc_was_enabled:
                gc.enable()
        try:
            assert tree is not None
            tree.close()
            closed_count = 0 if theme_view is None else theme_view.active_subscription_count
        finally:
            if context is not None:
                context.stop()
        return shown - started, constructed - started, shown - constructed, live_count, closed_count

    foundation = GuiFoundation.create(app, preferences)
    import transbridge.ui.main_window as main_window_module
    original_main_window = main_window_module.MainWindow

    def inject_foundation():
        def construct(*args, **kwargs):
            kwargs.setdefault("ui_foundation", foundation)
            return original_main_window(*args, **kwargs)
        return patch.object(main_window_module, "MainWindow", construct)

    baseline_window = ([], [], [], [], [])
    final_window = ([], [], [], [], [])
    for index in range(warmup + repetitions):
        # Alternate order to expose both variants to the same process/Qt drift.
        variants = ((baseline_window, None), (final_window, inject_foundation))
        if index % 2:
            variants = tuple(reversed(variants))
        for target, factory_patch in variants:
            measured = window_sample(factory_patch)
            if index >= warmup:
                for values, value in zip(target, measured):
                    values.append(value)

    baseline_window_samples = baseline_window[0]
    final_window_samples = final_window[0]

    injection = inject_foundation()
    injection.start()
    tree = fixture.build_representative_window_tree(
        entry_count=entries,
        warmup=warmup,
        repetitions=repetitions,
    )
    tree.show()
    try:
        light = ThemePreference(ThemeMode.LIGHT, DEFAULT_THEME_ID)
        dark = ThemePreference(ThemeMode.DARK, DEFAULT_THEME_ID)
        foundation.theme.set_preference(light, persist=False)
        foundation.theme.set_preference(dark, persist=False)

        switch_samples = []
        heartbeat_ticks = [time.perf_counter()]
        heartbeat = QTimer()
        heartbeat.setInterval(10)
        heartbeat.timeout.connect(lambda: heartbeat_ticks.append(time.perf_counter()))
        heartbeat.start()
        for index in range(repetitions):
            started = time.perf_counter()
            preference = light if index % 2 == 0 else dark
            result = foundation.theme.set_preference(preference, persist=False)
            app.processEvents()
            assert result.snapshot is not None
            switch_samples.append(time.perf_counter() - started)
        heartbeat_ticks.append(time.perf_counter())
        heartbeat.stop()
        heartbeat_gaps_ms = [
            (right - left) * 1000.0 for left, right in zip(heartbeat_ticks, heartbeat_ticks[1:])
        ]

        gc.collect()
        app.processEvents()
        gc.collect()
        rss_warmed = rss_bytes()
        icon_provider = IconProvider()
        for index in range(iterations):
            preference = light if index % 2 == 0 else dark
            result = foundation.theme.set_preference(preference, persist=False)
            assert result.snapshot is not None
            icon_provider.pixmap("activity", 16, 1.0, "normal", result.snapshot)
            app.processEvents()
        gc.collect()
        app.processEvents()
        gc.collect()
        rss_after = rss_bytes()
        rss_delta = max(0, rss_after - rss_warmed) if rss_warmed and rss_after else 0

        idle_scans = 0
        original_all_widgets = QApplication.allWidgets
        def counted_all_widgets():
            global idle_scans
            idle_scans += 1
            return original_all_widgets()
        idle_signals = []
        foundation.theme.theme_changed.connect(lambda revision, _snapshot: idle_signals.append(revision))
        with patch.object(QApplication, "allWidgets", staticmethod(counted_all_widgets)):
            for _ in range(20):
                app.processEvents()
        theme_timer_count = len(foundation.theme.findChildren(QTimer))
        idle_signal_count = len(idle_signals)

        foundation.theme.set_preference(light, persist=False)
        palette_applies = 0
        config_writes = 0
        noop_signals = []
        foundation.theme.theme_changed.connect(lambda revision, _snapshot: noop_signals.append(revision))
        original_set_palette = QApplication.setPalette
        original_save_theme = UiPreferenceRepository.save_theme_preference
        def counted_set_palette(self, palette):
            global palette_applies
            palette_applies += 1
            return original_set_palette(self, palette)
        def counted_save_theme(self, mode, theme_id):
            global config_writes
            config_writes += 1
            return original_save_theme(self, mode, theme_id)
        with patch.object(QApplication, "setPalette", counted_set_palette), patch.object(
            UiPreferenceRepository, "save_theme_preference", counted_save_theme
        ):
            for _ in range(iterations):
                result = foundation.theme.set_preference(light, persist=True)
                assert result.status.value == "unchanged"
        palette_cache_entries = len(foundation.theme._palette_cache)
        icon_stats = icon_provider.stats
    finally:
        tree.close()
        injection.stop()
        foundation.close()
        app.setPalette(original_palette)

report = {
    "schema_version": 1,
    "mode": "foundation-final",
    "authority": authority,
    "profile": {
        "fixture_schema": fixture.FIXTURE_SCHEMA_VERSION,
        "fixture_fingerprint": fixture.fixture_fingerprint(entry_count=entries),
        "entry_count": entries,
        "warmup": warmup,
        "repetitions": repetitions,
        "lifecycle_iterations": iterations,
        "platform_plugin": app.platformName(),
    },
    "cold_delta": {
        "samples_s": cold_samples,
        "p95_s": percentile(cold_samples, 0.95),
        "baseline_boundary_s": 0.0,
        "rss_delta_bytes": max(cold_rss_samples, default=0),
    },
    "window_open": {
        "baseline_samples_s": baseline_window_samples,
        "final_samples_s": final_window_samples,
        "baseline_p95_s": percentile(baseline_window_samples, 0.95),
        "final_p95_s": percentile(final_window_samples, 0.95),
        "baseline_construct_p95_s": percentile(baseline_window[1], 0.95),
        "final_construct_p95_s": percentile(final_window[1], 0.95),
        "baseline_show_p95_s": percentile(baseline_window[2], 0.95),
        "final_show_p95_s": percentile(final_window[2], 0.95),
        "final_live_subscriptions_max": max(final_window[3], default=0),
        "final_closed_subscriptions_max": max(final_window[4], default=0),
    },
    "hot_switch": {
        "samples_s": switch_samples,
        "p95_s": percentile(switch_samples, 0.95),
        "heartbeat_p95_ms": percentile(heartbeat_gaps_ms, 0.95),
        "heartbeat_max_ms": max(heartbeat_gaps_ms, default=0.0),
    },
    "lifecycle": {
        "iterations": iterations,
        "rss_warmed_bytes": rss_warmed,
        "rss_after_bytes": rss_after,
        "rss_delta_bytes": rss_delta,
        "palette_cache_entries": palette_cache_entries,
        "icon_cache_entries": icon_stats.entries,
        "icon_cache_cost_bytes": icon_stats.cost_bytes,
        "icon_cache_max_cost_bytes": icon_stats.max_cost_bytes,
    },
    "idle": {
        "window_tree_scans": idle_scans,
        "theme_timer_count": theme_timer_count,
        "theme_signals": idle_signal_count,
    },
    "noop_selection": {
        "iterations": iterations,
        "palette_applies": palette_applies,
        "theme_signals": len(noop_signals),
        "config_writes": config_writes,
    },
}
print(json.dumps(report, ensure_ascii=False, sort_keys=True))
"""


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_ui_foundation_cases_reuse_the_versioned_threshold_registry() -> None:
    thresholds = cases.THRESHOLDS_V1
    assert thresholds.ui_foundation_init_p95_s == 0.075
    assert thresholds.ui_foundation_rss_bytes == 12 * 1024 * 1024
    assert thresholds.ui_foundation_switch_p95_s == 0.250
    assert thresholds.ui_foundation_post_warmup_rss_bytes == 2 * 1024 * 1024
    assert thresholds.ui_heartbeat_p95_ms == 200.0
    assert thresholds.ui_modularization_max_regression_ratio == 0.05
    assert thresholds.ui_modularization_absolute_regression_s == 0.010
    assert thresholds.ui_lifecycle_iterations == 100

    cold = cases.get_case("ui-foundation-cold-init")
    opened = cases.get_case("ui-foundation-window-open")
    switched = cases.get_case("ui-foundation-theme-switch")
    noop = cases.get_case("ui-foundation-noop-switch")
    assert {cold.kind, opened.kind, switched.kind, noop.kind} == {"ui-foundation"}
    assert cold.threshold_p95 == thresholds.ui_foundation_init_p95_s
    assert switched.threshold_p95 == thresholds.ui_foundation_switch_p95_s
    assert noop.repetitions == thresholds.ui_lifecycle_iterations


def test_representative_fixture_fingerprint_and_current_composition_are_stable() -> None:
    fixture = _load_module("fr24_ui_foundation_fixture", FIXTURE_PATH)
    first = fixture.fixture_fingerprint()
    second = fixture.fixture_fingerprint()

    assert first == second
    assert len(first) == 64
    assert fixture.CANONICAL_ENTRY_COUNT == 10_000
    assert fixture.FIXTURE_SCHEMA_VERSION == 1
    components = fixture.REPRESENTATIVE_COMPONENTS
    for expected in (
        "MainWindow",
        "StartCenterWidget",
        "WorkbenchWidget",
        "Step2PreviewWidget",
        "GuidanceBanner",
        "ParaTranzWidget",
        "AITranslatorWindow",
        "ChatWidget",
        "TaskCenterPanel",
        "CommandPaletteDialog",
        "OperationPlanDialog",
        "FomodPanel",
    ):
        assert any(item.endswith(f".{expected}") for item in components)
    assert not any(item.endswith((".Step1Widget", ".Step3Widget")) for item in components)


def test_style_audit_is_stable_complete_and_machine_checkable() -> None:
    audit = _load_module("fr24_ui_foundation_audit", AUDIT_PATH)
    first, first_errors = audit.build_report(list(audit.DEFAULT_AUDIT_PATHS))
    second, second_errors = audit.build_report(list(audit.DEFAULT_AUDIT_PATHS))

    assert first_errors == second_errors == []
    assert first == second
    assert first["records"]
    keys = [(item["path"], item["line"], item["kind"]) for item in first["records"]]
    assert len(keys) == len(set(keys))
    assert all((REPO_ROOT / item["path"]).is_file() for item in first["records"])
    assert all(
        set(item) == {"path", "line", "kind", "subsystem", "risk", "snippet_hash", "status", "exemption"}
        for item in first["records"]
    )
    assert {"stylesheet", "qsettings", "accessibility", "rich_text"}.issubset(first["counts"])
    legacy = first["production_reachability"]["legacy"]
    assert legacy["transbridge.ui.workbench.step1"]["exists"]
    assert legacy["transbridge.ui.workbench.step3"]["exists"]
    assert not legacy["transbridge.ui.workbench.step1"]["reachable"]
    assert not legacy["transbridge.ui.workbench.step3"]["reachable"]


def test_final_audit_blocks_theme_ownership_and_unbounded_runtime_antipatterns() -> None:
    audit = _load_module("fr24_ui_foundation_final_audit", AUDIT_PATH)
    source = """
from PyQt6.QtCore import QSettings, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QWidget

class ThemeProbe(QWidget):
    def __init__(self):
        super().__init__()
        self._theme_cache = {}
        self._timer = QTimer(self)
        self._settings = QSettings("Example", "Theme")
        self.setStyleSheet("QWidget { color: #123456; background-image: url(icon.png); }")
        QColor("red")
        QApplication.allWidgets()
"""

    records, errors = audit.audit_source("src/transbridge/ui/theme_probe.py", source, final=True)

    blocked = {item.kind for item in records if item.status == "blocked"}
    assert {
        "color_qss",
        "hex_color",
        "raw_qt_color",
        "theme_polling",
        "theme_qsettings",
        "unbounded_theme_cache",
        "window_tree_scan",
    }.issubset(blocked)
    assert len(errors) == sum(item.status == "blocked" for item in records)


def test_final_audit_structured_exemptions_require_owner_reason_and_removal_gate() -> None:
    audit = _load_module("fr24_ui_foundation_exemption_audit", AUDIT_PATH)
    valid = audit.AuditExemption(
        rule="hex_color",
        path="src/transbridge/ui/data_fixture.py",
        symbol="*",
        owner="Domain data owner",
        reason="the literal is persisted user data, not presentation",
        expires_when="the data format stores canonical RGBA channels",
    )
    records, errors = audit.audit_source(
        "src/transbridge/ui/data_fixture.py",
        'LABEL_DATA = ("#123456",)\n',
        final=True,
        exemptions=(valid,),
    )
    assert errors == []
    assert records[0].status == "exempt"
    assert "owner=Domain data owner" in records[0].exemption
    assert "expires_when=" in records[0].exemption

    invalid = audit.AuditExemption(
        rule="hex_color",
        path="src/transbridge/ui/data_fixture.py",
        owner="",
        reason="",
        expires_when="never",
    )
    _records, invalid_errors = audit.audit_source(
        "src/transbridge/ui/data_fixture.py",
        'LABEL_DATA = ("#123456",)\n',
        final=True,
        exemptions=(invalid,),
    )
    assert any("requires owner" in error for error in invalid_errors)
    assert any("requires reason" in error for error in invalid_errors)
    assert any("removal condition" in error for error in invalid_errors)

    structural, structural_errors = audit.audit_source(
        "src/transbridge/ui/geometry_fixture.py",
        'widget.setStyleSheet("QWidget { background: transparent; border-radius: 6px; }")\n',
        final=True,
    )
    assert structural_errors == []
    assert {item.kind for item in structural} == {"stylesheet"}
    assert structural[0].status == "verified"


def test_final_audit_keeps_in_progress_ai_and_paratranz_inventory_non_blocking() -> None:
    audit = _load_module("fr24_ui_foundation_pending_audit", AUDIT_PATH)
    records, errors = audit.audit_paths(
        [
            REPO_ROOT / "src" / "transbridge" / "ui" / "tools" / "ai_translator",
            REPO_ROOT / "src" / "transbridge" / "ui" / "paratranz",
        ],
        final=True,
    )

    assert errors == []
    assert records
    assert all(item.status == "pending" for item in records)


def test_real_qt_baseline_runs_in_an_isolated_process_and_marks_its_authority() -> None:
    if importlib.util.find_spec("PyQt6") is None:
        pytest.skip("PyQt6 unavailable: real Qt window baseline cannot run")
    environment = os.environ.copy()
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    completed = subprocess.run(
        [
            sys.executable,
            str(FIXTURE_PATH),
            "--entries",
            "48",
            "--repetitions",
            "1",
            "--lifecycle-iterations",
            str(cases.THRESHOLDS_V1.ui_lifecycle_iterations),
        ],
        cwd=REPO_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    result = json.loads(completed.stdout)

    assert result["schema_version"] == 1
    assert result["mode"] == "baseline-no-foundation"
    assert result["authority"] in {"windows-visible", "partial-boundary"}
    assert result["profile"]["fixture_schema"] == 1
    assert result["profile"]["entry_count"] == 48
    assert result["profile"]["qt_version"]
    assert result["profile"]["pyqt_version"]
    assert result["profile"]["platform"]
    assert result["profile"]["font_family"]
    assert result["profile"]["logical_dpi"] > 0
    assert result["profile"]["device_pixel_ratio"] > 0
    assert result["cold_boundary"]["foundation_applied"] is False
    assert result["window_open"]["p50_s"] > 0
    assert result["window_open"]["p95_s"] > 0
    assert result["heartbeat_max_s"] >= 0
    assert result["noop_switch_control"]["iterations"] == cases.THRESHOLDS_V1.ui_lifecycle_iterations
    assert result["noop_switch_control"]["live_python_markers"] == 0
    if environment["QT_QPA_PLATFORM"] != "windows":
        assert result["authority"] == "partial-boundary"


def test_final_gui_foundation_profile_enforces_all_versioned_budgets(tmp_path: Path) -> None:
    """Exercise the real Foundation and representative tree without relaxing release budgets."""

    if importlib.util.find_spec("PyQt6") is None:
        pytest.skip("PyQt6 unavailable: final Foundation profile cannot run")
    environment = os.environ.copy()
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    environment["FR24_FIXTURE_PATH"] = str(FIXTURE_PATH)
    environment["FR24_LIFECYCLE_ITERATIONS"] = str(cases.THRESHOLDS_V1.ui_lifecycle_iterations)
    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(_FINAL_FOUNDATION_PROFILE)],
        cwd=REPO_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )
    result = json.loads(completed.stdout)
    thresholds = cases.THRESHOLDS_V1
    print(f"FR24_FINAL_PROFILE_JSON={completed.stdout.strip()}")

    assert result["schema_version"] == 1
    assert result["mode"] == "foundation-final"
    assert result["authority"] in {"windows-visible", "partial-boundary"}
    if environment["QT_QPA_PLATFORM"] != "windows":
        assert result["authority"] == "partial-boundary"
    assert result["cold_delta"]["p95_s"] <= thresholds.ui_foundation_init_p95_s
    assert result["cold_delta"]["rss_delta_bytes"] <= thresholds.ui_foundation_rss_bytes
    assert result["hot_switch"]["p95_s"] <= thresholds.ui_foundation_switch_p95_s
    assert result["hot_switch"]["heartbeat_p95_ms"] <= thresholds.ui_heartbeat_p95_ms

    baseline_p95 = result["window_open"]["baseline_p95_s"]
    final_p95 = result["window_open"]["final_p95_s"]
    relative_limit = baseline_p95 * (1.0 + thresholds.ui_modularization_max_regression_ratio)
    absolute_limit = baseline_p95 + thresholds.ui_modularization_absolute_regression_s
    assert final_p95 <= max(relative_limit, absolute_limit)

    lifecycle = result["lifecycle"]
    assert lifecycle["iterations"] == thresholds.ui_lifecycle_iterations
    assert lifecycle["rss_delta_bytes"] <= thresholds.ui_foundation_post_warmup_rss_bytes
    assert lifecycle["palette_cache_entries"] <= 2
    assert lifecycle["icon_cache_cost_bytes"] <= lifecycle["icon_cache_max_cost_bytes"]
    assert result["idle"] == {"theme_signals": 0, "theme_timer_count": 0, "window_tree_scans": 0}
    assert result["noop_selection"] == {
        "config_writes": 0,
        "iterations": thresholds.ui_lifecycle_iterations,
        "palette_applies": 0,
        "theme_signals": 0,
    }

    summary = {
        "authority": result["authority"],
        "cold_p95_s": result["cold_delta"]["p95_s"],
        "cold_rss_delta_bytes": result["cold_delta"]["rss_delta_bytes"],
        "window_baseline_p95_s": baseline_p95,
        "window_final_p95_s": final_p95,
        "window_baseline_construct_p95_s": result["window_open"]["baseline_construct_p95_s"],
        "window_final_construct_p95_s": result["window_open"]["final_construct_p95_s"],
        "window_baseline_show_p95_s": result["window_open"]["baseline_show_p95_s"],
        "window_final_show_p95_s": result["window_open"]["final_show_p95_s"],
        "window_live_subscriptions": result["window_open"]["final_live_subscriptions_max"],
        "window_closed_subscriptions": result["window_open"]["final_closed_subscriptions_max"],
        "switch_p95_s": result["hot_switch"]["p95_s"],
        "heartbeat_p95_ms": result["hot_switch"]["heartbeat_p95_ms"],
        "lifecycle_rss_delta_bytes": lifecycle["rss_delta_bytes"],
        "cache_cost_bytes": lifecycle["icon_cache_cost_bytes"],
    }
    summary_path = tmp_path / "fr24-final-performance.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    assert json.loads(summary_path.read_text(encoding="utf-8")) == summary
    summary_path.unlink()
    print(f"FR24_FINAL_PERF_JSON={json.dumps(summary, ensure_ascii=False, sort_keys=True)}")
