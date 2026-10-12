"""Real Qt-to-publication coverage for applying unsaved translations to another ESP."""

from dataclasses import replace
import os
from threading import Event, Thread, get_ident
from time import monotonic
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import QEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from tests.plugin_fixtures import write_plugin
from tests.ui.operations.test_production_facade import _Clock, _Ids
from transbridge.application.contracts import RequestContext
from transbridge.application.io.operation_write import _translation_entries
from transbridge.application.io.plugin_transfer import PluginTransferPreflightService
from transbridge.application.projects.provisioning import ProjectSourceRequest
from transbridge.application.tasks import CallbackThreadBackend, JobState, OwnerRef, TaskRuntime
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext
from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer
from transbridge.ui.operations.production import build_operation_plan_facade


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _wait_until(predicate):
    deadline = monotonic() + 5
    while not predicate() and monotonic() < deadline:
        QTest.qWait(10)
    assert predicate()


@pytest.fixture
def transfer_case(tmp_path, qapp):
    old = write_plugin(
        tmp_path / "old.esp", [(0x800, "Same", "Original"), (0x801, "Changed", "Before"), (0x802, "Gone", "Gone")]
    )
    selected = write_plugin(
        tmp_path / "new.esp", [(0x800, "Same", "Original"), (0x801, "Changed", "After"), (0x803, "New", "Added")]
    )
    prepared = TranslationIoProjectSourcePreparer().prepare_source(
        ProjectSourceRequest(str(old)), RequestContext("gui"), role="primary", common_options=()
    )
    hydration = prepared.hydration
    collection = TranslationEntryCollection(
        tuple(
            replace(entry, translation=f"译文 {index}", stage=1)
            for index, entry in enumerate(_translation_entries(hydration.entries))
        )
    )
    context = SimpleNamespace(
        active_slot=SimpleNamespace(
            source_snapshot=hydration.source_snapshot,
            format_id=hydration.format_id,
            collection=collection,
            esp_path=str(old),
        ),
        dirty=True,
        current_project={"name": "unsaved translation project"},
    )
    original_states = tuple(entry.snapshot() for entry in collection)
    queued = []
    tasks = TaskRuntime(
        id_generator=_Ids(),
        clock=_Clock(),
        backend=CallbackThreadBackend(lambda _run_id, target: queued.append(target)),
    )
    facade = build_operation_plan_facade(SimpleNamespace(tasks=tasks), RequestContext("gui"))
    output = tmp_path / "new_translated.esp"
    dialog = facade.begin_write(context, plugin_path=str(selected), target_path=str(output))
    case = SimpleNamespace(
        old=old,
        old_bytes=old.read_bytes(),
        selected=selected,
        selected_bytes=selected.read_bytes(),
        output=output,
        dialog=dialog,
        facade=facade,
        tasks=tasks,
        queued=queued,
        context=context,
        original_states=original_states,
        owner=OwnerRef("gui", "gui.operation-plan"),
    )
    yield case
    _wait_until(lambda: not facade._coordinator._preflight_workers)
    if not sip.isdeleted(dialog):
        dialog.set_execution_running(False)
        dialog.close()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("change_after_preview", [False, True])
def test_selected_esp_uses_background_preview_and_preserves_project(transfer_case, monkeypatch, change_after_preview):
    case = transfer_case
    entered, release = Event(), Event()
    gui_thread = get_ident()
    preflight = PluginTransferPreflightService.preflight

    def held_preflight(service, draft):
        assert get_ident() != gui_thread
        entered.set()
        assert release.wait(5)
        return preflight(service, draft)

    monkeypatch.setattr(PluginTransferPreflightService, "preflight", held_preflight)
    try:
        case.dialog._preflight_button.click()
        _wait_until(entered.is_set)
        assert not case.dialog._confirm_button.isEnabled()
        assert not case.output.exists()
    finally:
        release.set()
    _wait_until(lambda: not case.facade._coordinator._preflight_workers)
    assert case.dialog._confirm_button.isEnabled(), case.dialog._checks.text()
    if change_after_preview:
        write_plugin(case.selected, [(0x800, "Same", "External edit")])
    case.dialog._confirm_button.click()
    assert len(case.queued) == 1
    worker = Thread(target=case.queued.pop())
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()
    _wait_until(lambda: not case.dialog._execution_running)
    job = case.tasks.list(case.owner)[0]
    if change_after_preview:
        assert job.state is JobState.FAILED
        assert not case.output.exists()
        assert "变化" in case.dialog._checks.text()
    else:
        assert job.state is JobState.COMPLETED
        assert case.output.exists()
        texts = [
            item.string for item, _ in SSEPluginWithContext.from_file(case.output).extract_string_pairs_with_context()
        ]
        assert texts == ["译文 0", "After", "Added"]
        expected = write_plugin(
            case.output.with_name("expected.esp"),
            [(0x800, "Same", "译文 0"), (0x801, "Changed", "After"), (0x803, "New", "Added")],
        )
        assert case.output.read_bytes() == expected.read_bytes()
        assert str(case.output) in case.dialog._checks.text()
        assert case.dialog._checks.text().count(str(case.output)) == 1
        assert "已套用 1 条译文，目标保留 2 条" in case.dialog._checks.text()
        assert "将套用" not in case.dialog._checks.text()
        assert "TRANSFER_PRESERVED" not in case.dialog._checks.text()
        assert "PUBLISHED" not in case.dialog._checks.text()
        assert not case.dialog._summary.isVisible()
        assert not case.dialog._transfer_summary.isVisible()
        assert "目标原文已变化" in case.dialog._details.toPlainText()
        assert case.dialog._close_button.isEnabled()
        assert case.selected.read_bytes() == case.selected_bytes
        case.dialog._details_button.click()
        assert case.dialog._details.isVisible()
        case.dialog._select_plugin("")
        assert "选项已变化" in case.dialog._checks.text()
        assert not case.dialog._details.isVisible()
        assert not case.dialog._progress.isVisible()
    assert case.old.read_bytes() == case.old_bytes
    assert case.context.dirty
    assert case.context.current_project == {"name": "unsaved translation project"}
    assert tuple(entry.snapshot() for entry in case.context.active_slot.collection) == case.original_states


def test_closing_during_target_preview_does_not_start_write(transfer_case, qapp, monkeypatch):
    case = transfer_case
    entered, release = Event(), Event()
    preflight = PluginTransferPreflightService.preflight

    def held_preflight(service, draft):
        entered.set()
        assert release.wait(5)
        return preflight(service, draft)

    monkeypatch.setattr(PluginTransferPreflightService, "preflight", held_preflight)
    try:
        case.dialog._preflight_button.click()
        _wait_until(entered.is_set)
        assert case.dialog.close()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    finally:
        release.set()
    _wait_until(lambda: not case.facade._coordinator._preflight_workers)
    assert case.facade.active_plan_count == 0
    assert not case.tasks.list(case.owner)
    assert not case.output.exists()
