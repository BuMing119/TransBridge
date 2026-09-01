from __future__ import annotations

from pathlib import Path
from threading import Event
from time import monotonic
from types import SimpleNamespace

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.bootstrap import build_runtime
from transbridge.ui.context import AppContext
from transbridge.ui.main_window import MainWindow
from transbridge.ui.workbench.translation_table import COL_TRANSLATION

_APP = QApplication.instance() or QApplication([])


def _until(predicate):
    deadline = monotonic() + 8
    while not predicate() and monotonic() < deadline:
        QApplication.processEvents()
        QTest.qWait(5)
    assert predicate()


@pytest.fixture
def window_case(tmp_path, monkeypatch, request):
    application = _APP
    monkeypatch.setattr(
        "transbridge.paratranz.config_manager.ParatranzConfig.create_or_load",
        lambda: SimpleNamespace(token="", user_id=None),
    )
    monkeypatch.setattr("transbridge.ui.shell.window_lifecycle.WindowLifecycle.restore_state", lambda _self: None)
    monkeypatch.setattr(
        "transbridge.ui.coordinators.project_coordinator.workspace_path", lambda: tmp_path / "workspace.json"
    )
    root = tmp_path / "data"
    settings = {"persistence_v2_root": root, "ui_config_path": tmp_path / "config.ini"}
    runtime = build_runtime(settings)
    context = RequestContext("gui", run_id="variant-hydration-test")
    services = runtime.use_cases.resolve("persistence_v2")
    source = tmp_path / "first.xml"
    source.write_bytes((Path(__file__).parents[1] / "contracts/io/fixtures/eet-small.xml").read_bytes())
    commands = services.gui_project_commands
    assert commands.create_project(
        ProjectProvisioningRequest("Variants", "A", ProjectSourceRequest(str(source), FormatId.XML_EET)), context
    ).is_success
    if getattr(request, "param", 1) == 2:
        other = tmp_path / "second.xml"
        other.write_text(
            source
            .read_text(encoding="utf-8")
            .replace("Hello, traveler.", "A second source.")
            .replace("GreetingTopic", "SecondTopic")
            .replace("00000001", "00000002"),
            encoding="utf-8",
        )
        added = commands.add_source(ProjectSourceRequest(str(other), FormatId.XML_EET), context)
        assert added.is_success, added.diagnostics
    first = services.project_lifecycle.active.variant.ref
    keys = tuple(item.entry_key for item in services.project_lifecycle.active.variant.snapshot().entries)
    for key in keys:
        assert commands.update_entry(key, context, translation="Variant A", stage=1).is_success
    assert commands.save(context).is_success
    assert commands.create_variant("B", context, copy_active=True).is_success
    second = services.project_lifecycle.active.variant.ref
    for key in keys:
        assert commands.update_entry(key, context, translation="Variant B", stage=3).is_success
    assert commands.save(context).is_success
    project = services.project_lifecycle.active.project_ref
    assert commands.switch_v2(project, first, context).is_success
    runtime.close()

    # Rebuild the runtime and open the real MainWindow from the persisted active pointer.
    runtime = build_runtime(settings)
    services = runtime.use_cases.resolve("persistence_v2")
    app_context = AppContext(
        project_projection=services.project_projection,
        project_commands=services.gui_project_commands,
        runtime_context=context,
    )
    window = MainWindow(app_context=app_context, runtime=runtime, runtime_context=context)
    _until(lambda: window.project_open_worker is None and bool(window.context.collection))
    yield SimpleNamespace(
        window=window, services=services, context=context, first=first, second=second, source=source, app=application
    )
    _until(
        lambda: window.foreground_worker is None and window.project_open_worker is None and window.save_worker is None
    )
    window._window_lifecycle.auto_saver.stop()
    window.tool_windows.dispose(wait_for_worker=True)
    window.context.close_projection()
    window.status_presenter.close()
    window._window_lifecycle._close_ready = True
    window.close()
    window.deleteLater()
    runtime.close()
    application.processEvents()


@pytest.mark.parametrize("window_case", [1, 2], indirect=True)
def test_real_cold_start_and_variant_combo_rehydrate_eet_and_allow_save(window_case):
    case = window_case
    window = case.window
    assert next(iter(window.context.collection)).translation == "Variant A"
    assert window.context.active_slot.format_id is FormatId.XML_EET
    combo = window.workbench.project_bar._variant_combo
    combo.setCurrentIndex(combo.findData(case.second.identity.value))
    _until(lambda: window.foreground_worker is None and window.context.active_variant_id == case.second.identity.value)

    assert next(iter(window.context.collection)).translation == "Variant B"
    assert next(iter(window.context.collection)).stage == 3
    assert window.context.active_slot.source_snapshot is not None
    assert not window.context.authoritative_projection_diverged()
    for location, slot in window.context.slots.items():
        assert next(iter(slot.collection)).translation == "Variant B"
        assert slot.source_snapshot is not None
        window.context.activate_slot(location)
        table = window.workbench.preview._table
        _until(lambda: table.rowCount() == 1 and table.item(0, COL_TRANSLATION) is not None)
        assert table.item(0, COL_TRANSLATION).text() == "Variant B"
    saved = []
    assert window.save_current_project_async(on_finished=saved.append)
    _until(lambda: bool(saved))
    assert saved == [True]

    combo.setCurrentIndex(combo.findData(case.first.identity.value))
    _until(lambda: window.foreground_worker is None and window.context.active_variant_id == case.first.identity.value)
    assert next(iter(window.context.collection)).translation == "Variant A"
    assert not window.context.authoritative_projection_diverged()


def test_failed_variant_source_restore_does_not_leave_old_variant_editable(window_case):
    case = window_case
    case.source.unlink()
    combo = case.window.workbench.project_bar._variant_combo
    combo.setCurrentIndex(combo.findData(case.second.identity.value))
    _until(
        lambda: (
            case.window.foreground_worker is None
            and case.window.context.active_variant_id == case.second.identity.value
        )
    )

    assert not case.window.context.slots
    assert case.services.project_lifecycle.active.variant.snapshot().entries[0].translation == "Variant B"


def test_late_hydration_cannot_replace_a_newer_variant_and_save_waits_for_hydration(window_case, monkeypatch):
    case = window_case
    started, release = Event(), Event()
    opener = case.window.current_project_opener
    prepare = opener.prepare_active

    def blocked(context):
        result = prepare(context)
        started.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(opener, "prepare_active", blocked)
    combo = case.window.workbench.project_bar._variant_combo
    combo.setCurrentIndex(combo.findData(case.second.identity.value))
    _until(started.is_set)
    assert not case.window.save_current_project_async()
    assert not case.window.workbench.isEnabled()
    active = case.services.project_lifecycle.active
    try:
        assert case.window.project_commands.switch_v2(active.project_ref, case.first, case.context).is_success
    finally:
        release.set()
    _until(lambda: case.window.foreground_worker is None)

    assert case.window.context.active_variant_id == case.first.identity.value
    assert not case.window.context.slots
    assert case.services.project_lifecycle.active.variant.snapshot().entries[0].translation == "Variant A"
