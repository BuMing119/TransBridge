"""Real upload receipts must leave every workbench source safe to save."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
import pytest

from tests.ui.operations import test_categorized_upload_bridge as bridge
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.context import AppContext, CollectionSlot
from transbridge.ui.main_window import MainWindow
from transbridge.ui.workbench.step2 import Step2PreviewWidget

project_fixture = bridge.project_fixture
harness = bridge.harness
_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def workbench(harness, monkeypatch):
    monkeypatch.setattr("transbridge.ui.context.ParatranzConfig.create_or_load", lambda: SimpleNamespace(token=""))
    context = AppContext(
        project_projection=harness.services.project_projection,
        project_commands=harness.services.gui_project_commands,
        runtime_context=harness.request_context,
    )
    snapshot = bridge._snapshot(harness)
    for source in snapshot.sources:
        context.add_slot(source.source_id, CollectionSlot(source.label, TranslationEntryCollection(source.entries)))
    host = SimpleNamespace(
        _ctx=context,
        _project_commands=harness.services.gui_project_commands,
        _runtime_context=harness.request_context,
    )
    host.preview = Step2PreviewWidget(context)
    host.preview.refresh(context.collection)
    assert not context.authoritative_projection_diverged()
    yield context, host
    host.preview.close()
    context.close_projection()
    _APP.processEvents()


def _background_upload(harness):
    # Hold Qt delivery until after commit, as occurs with queued worker signals.
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(bridge._upload, harness).result(timeout=10)
    _APP.processEvents()
    return result


@pytest.mark.parametrize("cancel_after_first", [False, True])
def test_upload_receipts_reach_all_workbench_slots_before_save(harness, workbench, monkeypatch, cancel_after_first):
    context, host = workbench
    generation = host.preview._table.render_session.generation
    before = {entry.identity: entry.snapshot() for slot in context.slots.values() for entry in slot.collection}
    if cancel_after_first:
        create = harness.remote.create_file

        def cancel(*args):
            result = create(*args)
            harness.cancel.set()
            return result

        monkeypatch.setattr(harness.remote, "create_file", cancel)

    receipt = _background_upload(harness)

    assert receipt.success_count == (1 if cancel_after_first else 2)
    assert not context.authoritative_projection_diverged()
    assert MainWindow._save_current_project(host).is_success
    assert host.preview._table.render_session.generation == generation
    for row in range(host.preview._table.rowCount()):
        displayed = host.preview._table.model().index(row, 0).data(Qt.ItemDataRole.UserRole)
        assert displayed is context.collection.get(displayed.identity)
    states = {entry.entry_key: entry for entry in harness.services.project_lifecycle.active.variant.snapshot().entries}
    for slot in context.slots.values():
        for entry in slot.collection:
            assert (entry.external_refs, entry.revision) == (
                states[entry.identity].external_refs,
                states[entry.identity].revision,
            )
            assert (entry.translation, entry.stage) == (
                before[entry.identity].translation,
                before[entry.identity].stage,
            )
            for reference in entry.external_refs:
                assert slot.collection.get_by_external_ref(reference) == (entry,)


def test_receipt_publication_does_not_hide_uncommitted_workbench_edit(harness, workbench):
    context, host = workbench
    entry = next(iter(context.collection))
    context.collection = TranslationEntryCollection((replace(entry, translation="尚未提交的本地编辑", stage=3),))

    assert _background_upload(harness).success_count == 2

    assert context.collection.get(entry.identity).translation == "尚未提交的本地编辑"
    assert context.collection.get(entry.identity).stage == 3
    assert context.authoritative_projection_diverged()
    saved = MainWindow._save_current_project(host)
    assert not saved.is_success
    assert saved.diagnostics[0].code == "PROJECTION_AUTHORITY_DIVERGED"


def test_upload_does_not_depend_on_receipt_save_or_project_remote_ids(harness, workbench, monkeypatch):
    context, _host = workbench
    collections = tuple(slot.collection for slot in context.slots.values())

    def fail(*_args):
        raise OSError("receipt storage unavailable")

    monkeypatch.setattr(harness.services.project_lifecycle, "commit_active_content", fail)

    assert _background_upload(harness).success_count == 2

    assert tuple(slot.collection for slot in context.slots.values()) == collections
    assert all(not entry.external_refs for collection in collections for entry in collection)
    assert not context.authoritative_projection_diverged()
