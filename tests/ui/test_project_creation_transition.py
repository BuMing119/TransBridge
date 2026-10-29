from __future__ import annotations

from dataclasses import replace
import os
from types import SimpleNamespace
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from tests.plugin_fixtures import write_plugin
from transbridge.application.contracts import RequestContext
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.context import AppContext
from transbridge.ui.coordinators.guided_project_coordinator import GuidedDraftPhase
from transbridge.ui.main_window import MainWindow
from transbridge.ui.shell.start_center import StartCenterWidget
from transbridge.ui.shell.start_center_controller import StartCenterController

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def creation(tmp_path_factory, monkeypatch):
    tmp_path = tmp_path_factory.mktemp("creation")
    monkeypatch.setattr("transbridge.ui.context.ParatranzConfig.create_or_load", lambda: SimpleNamespace(token=""))
    services = build_persistence_v2_services(
        tmp_path / "data", id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "2026-10-20T00:00:00Z"
    )
    request = RequestContext("gui", run_id="creation-transition", authorized_roots=(str(tmp_path),))
    context = AppContext(
        project_projection=services.project_projection,
        project_commands=services.gui_project_commands,
        runtime_context=request,
    )
    view = StartCenterWidget()
    messages = []
    host = SimpleNamespace(
        context=context,
        _ctx=context,
        project_commands=services.gui_project_commands,
        _project_commands=services.gui_project_commands,
        runtime_context=request,
        _runtime_context=request,
        app_runtime=SimpleNamespace(use_cases=SimpleNamespace(resolve=lambda _: services.project_projection)),
        _foreground_worker=None,
        _save_worker=None,
        _project_open_worker=None,
        _workers=[],
        _workbench=SimpleNamespace(
            show_step2_progress=lambda *_: None, hide_step2_progress=lambda: None, setEnabled=lambda _: None
        ),
        show_message=lambda message, **_: messages.append(message),
        mode_tabs=SimpleNamespace(setCurrentWidget=lambda _: None, setCurrentIndex=lambda _: None),
    )
    host.start_foreground_task = lambda *args, **kwargs: MainWindow.start_foreground_task(host, *args, **kwargs)
    controller = StartCenterController(host, view)
    controller.start()
    yield SimpleNamespace(
        host=host, controller=controller, view=view, services=services, context=context, root=tmp_path
    )
    for worker in host._workers:
        worker.wait(5_000)
    _APP.processEvents()
    view.close()
    context.close_projection()
    services.close()


def _create(creation, name, paths=(), *, migrations=()):
    creation.controller.guided_project.begin_sources(tuple(str(path) for path in paths))
    creation.controller.guided_project.set_project_name(name)
    creation.controller.guided_project.set_migration_sources(tuple(str(path) for path in migrations))
    creation.view._draft_primary.click()
    for _ in range(500):
        QTest.qWait(10)
        if not creation.controller.guided_project.state.in_flight and creation.host._foreground_worker is None:
            break
    assert not creation.controller.guided_project.state.in_flight
    return creation.controller.guided_project.state


@pytest.mark.parametrize("source_count", [0, 1, 2])
def test_new_project_replaces_all_old_slots_and_remains_saveable(creation, source_count):
    old = write_plugin(creation.root / "Addon.esp", [(0x800, "OldNPC", "Old original")])
    assert _create(creation, "Addon", (old,)).phase is GuidedDraftPhase.COMPLETED
    old_project = creation.context.active_project_id
    assert MainWindow._save_current_project(creation.host).is_success
    observations = []
    creation.context.project_changed.connect(
        lambda: observations.append((creation.context.project_name, tuple(creation.context.slots)))
    )
    paths = tuple(
        write_plugin(creation.root / f"New{i}.esp", [(0x900 + i, f"NewNPC{i}", f"New original {i}")])
        for i in range(source_count)
    )

    assert _create(creation, "Skyrim", paths).phase is GuidedDraftPhase.COMPLETED

    assert creation.context.active_project_id != old_project
    expected = tuple(source["source_id"] for source in creation.context.project_sources)
    assert tuple(creation.context.slots) == expected
    assert tuple(slot.esp_path for slot in creation.context.slots.values()) == tuple(
        str(path.resolve()) for path in paths
    )
    assert observations == [("Skyrim", expected)]
    assert sum(len(slot.collection) for slot in creation.context.slots.values()) == source_count
    assert not creation.context.project_opening
    assert not creation.context.project_view_failed
    assert not creation.context.authoritative_projection_diverged()
    assert MainWindow._save_current_project(creation.host).is_success
    assert old.is_file()


def test_invalid_new_project_keeps_previous_workbench_and_saved_content(creation):
    old = write_plugin(creation.root / "Addon.esp", [(0x800, "OldNPC", "Old original")])
    assert _create(creation, "Addon", (old,)).phase is GuidedDraftPhase.COMPLETED
    original_slot = creation.context.active_slot
    original_id = creation.context.active_project_id
    broken = creation.root / "Broken.esp"
    broken.write_bytes(b"invalid plugin")

    assert _create(creation, "Broken", (broken,)).phase is GuidedDraftPhase.FAILED

    assert creation.context.active_project_id == original_id
    assert creation.context.active_slot is original_slot
    assert not creation.context.project_opening
    assert not creation.context.authoritative_projection_diverged()
    assert MainWindow._save_current_project(creation.host).is_success


def test_failed_view_preparation_retains_old_slots_and_blocks_edits(creation, monkeypatch):
    old = write_plugin(creation.root / "Addon.esp", [(0x800, "OldNPC", "Old original")])
    assert _create(creation, "Addon", (old,)).phase is GuidedDraftPhase.COMPLETED
    original_slot = creation.context.active_slot
    new = write_plugin(creation.root / "Skyrim.esm", [(0x900, "NewNPC", "New original")])

    def fail(*_args):
        raise ValueError("injected view failure")

    monkeypatch.setattr("transbridge.ui.coordinators.project_creation_operation.prepare_project_slots", fail)
    state = _create(creation, "Skyrim", (new,))

    assert state.phase is GuidedDraftPhase.FAILED
    assert state.diagnostic_code == "PROJECT_VIEW_INSTALL_FAILED"
    assert "重新打开工程" in state.diagnostic_message
    assert creation.context.active_slot is original_slot
    assert creation.context.project_view_failed
    assert not creation.context.project_opening
    assert not MainWindow._save_current_project(creation.host).is_success


@pytest.mark.parametrize("commit_to_authority", [False, True])
def test_new_project_cannot_drop_old_unsaved_translation(creation, commit_to_authority):
    old = write_plugin(creation.root / "Addon.esp", [(0x800, "OldNPC", "Old original")])
    assert _create(creation, "Addon", (old,)).phase is GuidedDraftPhase.COMPLETED
    old_id = creation.context.active_project_id
    entry = next(iter(creation.context.collection))
    if commit_to_authority:
        assert creation.context.update_projected_entry(entry.identity, translation="未保存译文", stage=1).is_success
    creation.context.collection = TranslationEntryCollection((replace(entry, translation="未保存译文", stage=1),))
    new = write_plugin(creation.root / "Skyrim.esm", [(0x900, "NewNPC", "New original")])

    state = _create(creation, "Skyrim", (new,))

    assert state.phase is GuidedDraftPhase.FAILED
    assert state.diagnostic_code == ("ACTIVE_SAVE_REQUIRED" if commit_to_authority else "PROJECTION_AUTHORITY_DIVERGED")
    assert creation.context.active_project_id == old_id
    assert next(iter(creation.context.collection)).translation == "未保存译文"
    assert not creation.context.project_opening


@pytest.mark.parametrize("with_primary", [False, True])
def test_independent_migration_sources_are_included_in_the_new_workbench(creation, with_primary):
    old = write_plugin(creation.root / "Old.esp", [(0x800, "OldNPC", "Old original")])
    assert _create(creation, "Old", (old,)).phase is GuidedDraftPhase.COMPLETED
    primary = write_plugin(creation.root / "New.esp", [(0x900, "NewNPC", "New original")])
    translated = write_plugin(creation.root / "Translated.esp", [(0xA00, "TranslatedNPC", "已有译文")])

    state = _create(creation, "New", (primary,) if with_primary else (), migrations=(translated,))

    assert state.phase is GuidedDraftPhase.COMPLETED
    assert set(creation.context.slots) == {source["source_id"] for source in creation.context.project_sources}
    assert {slot.esp_path for slot in creation.context.slots.values()} == {str(translated.resolve())} | (
        {str(primary.resolve())} if with_primary else set()
    )
    migrated_slot = next(slot for slot in creation.context.slots.values() if slot.esp_path == str(translated.resolve()))
    migrated = next(iter(migrated_slot.collection))
    assert migrated.translation == "已有译文"
    assert migrated.stage == 1
    assert not creation.context.authoritative_projection_diverged()
    assert MainWindow._save_current_project(creation.host).is_success
