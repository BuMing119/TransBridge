"""Restore binds popup editing immediately and prepares task context on first use."""

from dataclasses import replace
from threading import Event
from types import SimpleNamespace

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QWidget
import pytest

from tests.dialogue_catalog_support import dialogue_plugin, dialogue_plugin_bytes
from tests.dialogue_support import dialogue_entries
from tests.ui.test_dialogue_editor import drain
from transbridge.application.dialogue import loading
from transbridge.application.io.contracts import FormatId, SourceDescriptor, SourceSnapshot
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui import context as context_module
from transbridge.ui.dialogue.controller import DialogueEditorController
from transbridge.ui.workbench.step2 import Step2PreviewWidget
from transbridge.ui.workbench.translation_table_columns import COL_ORIGINAL


@pytest.fixture
def lazy_editor(monkeypatch):
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token=""))
    context = context_module.AppContext()
    collection = TranslationEntryCollection(dialogue_entries())
    context.add_slot(
        "fixture.esp",
        context_module.CollectionSlot("Test", collection, esp_path="fixture.esp", plugin=dialogue_plugin()),
    )
    parent = QWidget()
    preview = Step2PreviewWidget(context, parent)
    preview.refresh(collection)
    controller = DialogueEditorController(context, parent, preview, [])
    yield controller
    drain(controller)
    controller.close()
    preview.close()
    parent.close()
    parent.deleteLater()


@pytest.mark.parametrize("source_kind", ["plugin", "snapshot"])
def test_restore_does_no_index_work_then_first_use_builds_once_and_source_change_rebuilds(
    lazy_editor, monkeypatch, source_kind
):
    editor = lazy_editor
    slot = editor.context.active_slot
    if source_kind == "snapshot":
        slot.plugin = None
        slot.source_snapshot = SourceSnapshot.from_bytes(
            SourceDescriptor("fixture.esp"), FormatId.PLUGIN_SSE, dialogue_plugin_bytes()
        )
    builds, reads = [], []
    build, read = editor._loader.build, loading.read_dialogue_catalog

    def counted_build(*args, **kwargs):
        builds.append(True)
        return build(*args, **kwargs)

    def counted_read(plugin):
        reads.append(True)
        return read(plugin)

    monkeypatch.setattr(editor._loader, "build", counted_build)
    monkeypatch.setattr(loading, "read_dialogue_catalog", counted_read)
    for _ in range(3):
        editor.refresh()
    drain(editor)
    assert not builds and not reads
    assert not editor._entry_keys
    assert not editor._index_structure.keys
    assert editor.view.table_model.rowCount() == 0
    # Popup activation works before there has ever been a task relationship index.
    table = editor.preview._table
    assert table.activate_entry_editor(table.model().index(2, COL_ORIGINAL))
    drain(editor)
    assert len(builds) == len(reads) == 1
    assert editor.view.context_panel.isEnabled()
    original_index = editor._index
    editor.dialog.close()
    collection = editor.context.collection
    with pytest.warns(DeprecationWarning):
        collection.add(replace(collection.get(dialogue_entries()[2].identity), translation="external"))
    editor.refresh()
    editor.open_entry(dialogue_entries()[2].identity)
    drain(editor)
    assert len(builds) == len(reads) == 1
    assert editor._index is original_index
    assert editor.view.translation.toPlainText() == "external"
    editor.dialog.close()
    if source_kind == "plugin":
        slot.plugin = dialogue_plugin()
    else:
        slot.source_snapshot = SourceSnapshot.from_bytes(
            SourceDescriptor("fixture.esp"), FormatId.PLUGIN_SSE, dialogue_plugin_bytes("ChangedScene")
        )
    editor.refresh()
    assert len(builds) == len(reads) == 1
    editor.open_entry(dialogue_entries()[2].identity)
    drain(editor)
    assert len(builds) == len(reads) == 2
    assert editor._index is not original_index


def test_hidden_restore_does_not_iterate_collection(lazy_editor, monkeypatch):
    def unexpected_iteration(_collection):
        pytest.fail("hidden editor traversed the collection")

    monkeypatch.setattr(TranslationEntryCollection, "__iter__", unexpected_iteration)
    lazy_editor.refresh()
    assert dialogue_entries()[2].identity in lazy_editor.preview._table._editable_entry_keys


def test_first_use_keeps_editing_responsive_and_closing_does_not_reopen_after_delivery(lazy_editor, monkeypatch):
    editor = lazy_editor
    started, release = Event(), Event()
    original = editor._loader.build

    def slow_build(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(editor._loader, "build", slow_build)
    try:
        editor.open_entry(dialogue_entries()[2].identity)
        assert started.wait(3)
        delivered = []
        QTimer.singleShot(0, lambda: delivered.append(True))
        editor.view.translation.setPlainText("typing while parsing")
        QApplication.processEvents()
        assert delivered
        assert editor.view.translation.toPlainText() == "typing while parsing"
        editor.discard()
        editor.dialog.close()
    finally:
        release.set()
        drain(editor)
    assert not editor.dialog.isVisible()
    assert editor._current is None


def test_structural_metadata_change_rebuilds_but_reuses_source_catalog(lazy_editor, monkeypatch):
    editor = lazy_editor
    reads = []
    original = loading.read_dialogue_catalog

    def counted_read(plugin):
        reads.append(True)
        return original(plugin)

    monkeypatch.setattr(loading, "read_dialogue_catalog", counted_read)
    target = dialogue_entries()[2].identity
    editor.open_entry(target)
    drain(editor)
    old_index, generation = editor._index, editor._generation
    entry = editor.context.collection.get(target)
    with pytest.warns(DeprecationWarning):
        editor.context.collection.add(replace(entry, context="INFO|00000002"))
    editor.refresh()
    drain(editor)
    assert editor._generation > generation
    assert editor._index is not old_index
    assert len(reads) == 1


def test_translation_delta_in_open_editor_does_not_walk_unchanged_entries(lazy_editor, monkeypatch):
    editor = lazy_editor
    target = dialogue_entries()[2].identity
    editor.open_entry(target)
    drain(editor)
    original_index, generation = editor._index, editor._generation
    entry = editor.context.collection.get(target)
    with pytest.warns(DeprecationWarning):
        editor.context.collection.add(replace(entry, translation="new translation"))

    def unexpected_iteration(_collection):
        pytest.fail("translation delta caused a full relationship snapshot")

    monkeypatch.setattr(TranslationEntryCollection, "__iter__", unexpected_iteration)
    editor.refresh()
    assert editor._index is original_index
    assert editor._generation == generation
    assert editor.view.translation.toPlainText() == "new translation"


def test_failed_first_use_can_retry_without_rebuilding_again_on_next_refresh(lazy_editor, monkeypatch):
    editor = lazy_editor
    target = dialogue_entries()[2].identity
    calls = []
    original = editor._loader.build

    def fail_once(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise ValueError("invalid first source read")
        return original(*args, **kwargs)

    monkeypatch.setattr(editor._loader, "build", fail_once)
    editor.open_entry(target)
    drain(editor)
    assert "invalid first source read" in editor.view.context_reason.text()
    assert editor.view.translation.isEnabled()
    editor.open_entry(target)
    drain(editor)
    assert editor.view.context_panel.isEnabled()
    index = editor._index
    editor.refresh()
    drain(editor)
    assert len(calls) == 2
    assert editor._index is index
