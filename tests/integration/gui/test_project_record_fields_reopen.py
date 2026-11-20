"""Record-field browsing uses durable project entries after the plugin goes offline."""

from contextlib import contextmanager
import struct
from threading import Event

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QMessageBox
import pytest

from tests.dialogue_catalog_support import field, plugin_bytes, record, reference
from tests.integration.gui import test_project_paged_workbench as paged
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.persistence.project_store.paths import project_path
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.ui.dialogue.controller import DialogueEditorController
from transbridge.ui.project_rows import ProjectRowsCollection
from transbridge.ui.workbench.translation_table_columns import COL_ORIGINAL

qapp = paged.qapp


def _plugin():
    return plugin_bytes({
        "BOOK": record(
            "BOOK",
            0x100,
            field("EDID", b"BookEditor\0") + field("FULL", b"Book title\0") + field("DESC", b"Book text\0"),
        ),
        "MGEF": record(
            "MGEF",
            0x101,
            field("EDID", b"EffectEditor\0")
            + field("FULL", b"Effect title\0")
            + field("DNAM", b"Effect description\0"),
        ),
        "QUST": record(
            "QUST",
            1,
            field("EDID", b"QuestEditor\0")
            + field("FULL", b"Quest title\0")
            + field("INDX", struct.pack("<I", 10))
            + field("CNAM", b"Journal one\0")
            + field("INDX", struct.pack("<I", 20))
            + field("CNAM", b"Journal two\0")
            + field("QOBJ", struct.pack("<H", 10))
            + field("NNAM", b"Objective one\0")
            + field("QOBJ", struct.pack("<H", 20))
            + field("NNAM", b"Objective two\0"),
        ),
        "DIAL": record(
            "DIAL", 0x10, field("EDID", b"QuestTopic\0") + reference("QNAM", 1) + field("FULL", b"Topic prompt\0")
        ),
    })


@contextmanager
def _reopened_editor(tmp_path, app):
    root = tmp_path / "data"
    source = tmp_path / "Fields.esp"
    original = _plugin()
    source.write_bytes(original)
    setup = paged._services(root)
    try:
        result = setup.gui_project_commands.create_project(
            ProjectProvisioningRequest("Record fields", source=ProjectSourceRequest(str(source))), paged._CONTEXT
        )
        assert result.is_success, result.diagnostics
        active = setup.project_lifecycle.active
        database = project_path(root, active.project_ref.identity)
        variant = active.formal_variant_ref.identity
    finally:
        setup.close()
    assert source.read_bytes() == original
    source.unlink()

    with paged._workbench(root, app) as (host, coordinator, services):
        failures = paged._begin_open(host, coordinator, database)
        paged._wait_until(app, lambda: host.project_open_worker is None)
        assert not failures, failures
        assert isinstance(host.context.collection, ProjectRowsCollection)
        assert host.context.active_slot.plugin is None
        assert host.context.active_slot.source_snapshot.content is None
        preview = host.workbench.preview
        paged._wait_until(app, lambda: preview._paging.resident_ready)
        editor = DialogueEditorController(
            host.context, host.workbench, preview, host.workers, projection=services.project_projection
        )
        try:
            yield editor, services, database, variant
        finally:
            editor.close()
            paged._wait_until(app, lambda: editor._delivery is None)


def _double_click_filtered(editor, app, original):
    preview = editor.preview
    preview._filters_view.search_original.setText(original)
    paged._wait_until(app, lambda: preview.get_filtered_count() == 1)
    table = preview._table
    paged._wait_until(app, lambda: table.entry_at_row(0) is not None)
    target = table.entry_at_row(0).identity
    index = table.model().index(0, COL_ORIGINAL)
    table.scrollTo(index)
    app.processEvents()
    point = table.visualRect(index).center()
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    paged._wait_until(app, lambda: editor.dialog.isVisible() and editor._delivery is None)
    assert editor._selected_key == target
    assert editor._current.before.entry_key == target
    assert editor.view.original.toPlainText() == original
    return target


def _select_record_field(editor, app, original):
    model = editor.view.record_model
    paged._wait_until(app, lambda: any(entry.original == original for entry in model.entries))
    row = next(index for index, entry in enumerate(model.entries) if entry.original == original)
    table = editor.view.record_table
    index = model.index(row, 1)
    table.scrollTo(index)
    app.processEvents()
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualRect(index).center())
    assert editor._selected_key == model.entries[row].identity
    assert editor.view.original.toPlainText() == original
    return editor._selected_key


@pytest.mark.parametrize(
    ("kind", "editor_id", "form_id", "title", "body", "body_field"),
    [
        ("BOOK", "BookEditor", "00000100", "Book title", "Book text", "DESC"),
        ("MGEF", "EffectEditor", "00000101", "Effect title", "Effect description", "DNAM"),
    ],
)
def test_filtered_reopened_record_keeps_fields_and_independent_drafts(
    tmp_path, qapp, monkeypatch, kind, editor_id, form_id, title, body, body_field
):
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: QMessageBox.StandardButton.Cancel)
    with _reopened_editor(tmp_path, qapp) as (editor, services, database, variant):
        store = ProjectStore(database)
        original_rows = tuple(store.iter_entries(variant))
        body_key = _double_click_filtered(editor, qapp, body)
        view = editor.view
        paged._wait_until(qapp, lambda: view.record_model.rowCount() == 2)
        assert view.browsing_mode == "record"
        assert view.record_table.isVisible()
        assert not view.context_reason.isVisible()
        assert tuple(entry.original for entry in view.record_model.entries) == (title, body)
        assert all(value in view.record_heading.text() for value in (kind, editor_id, form_id))
        assert "FULL" in view.record_model.index(0, 0).data()
        assert body_field in view.record_model.index(1, 0).data()
        assert view.record_table.currentIndex().row() == 1

        view.translation.setPlainText("Body draft")
        title_key = _select_record_field(editor, qapp, title)
        assert view.translation.toPlainText() == ""
        view.translation.setPlainText("Title applied")
        _select_record_field(editor, qapp, body)
        assert view.translation.toPlainText() == "Body draft"
        _select_record_field(editor, qapp, title)
        assert view.translation.toPlainText() == "Title applied"
        assert tuple(store.iter_entries(variant)) == original_rows

        # Applying this field cannot publish another field's pending draft.
        view.apply_button.click()
        assert editor.dialog.isVisible()  # The independent body draft protects closing.
        assert editor.context.collection.get(title_key).translation == "Title applied"
        assert editor.context.collection.get(body_key).translation == ""
        _select_record_field(editor, qapp, body)
        assert view.translation.toPlainText() == "Body draft"
        view.translation.setPlainText("Body applied")
        view.apply_button.click()
        assert not editor.dialog.isVisible()
        saved = services.gui_project_commands.save(paged._CONTEXT)
        assert saved.is_success, saved.diagnostics
        assert editor.preview._filters_view.search_original.text() == body
        persisted = {row.state.entry_key: row.state.translation for row in store.iter_entries(variant)}
        assert persisted[title_key] == "Title applied"
        assert persisted[body_key] == "Body applied"
        assert all(not text for key, text in persisted.items() if key not in {title_key, body_key})


def test_quest_occurrences_and_draft_survive_manual_record_dialogue_tab_switching(tmp_path, qapp):
    with _reopened_editor(tmp_path, qapp) as (editor, _services, database, variant):
        before = tuple(ProjectStore(database).iter_entries(variant))
        target = _double_click_filtered(editor, qapp, "Journal two")
        view = editor.view
        assert view.browsing_mode == "record"
        paged._wait_until(qapp, lambda: view.record_model.rowCount() == 5)
        bar = view.browse_tabs.tabBar()
        assert editor._selected_key == target
        assert tuple(entry.original for entry in view.record_model.entries) == (
            "Quest title",
            "Journal one",
            "Journal two",
            "Objective one",
            "Objective two",
        )
        labels = tuple(view.record_model.index(row, 0).data() for row in range(5))
        assert labels[1] != labels[2] and "CNAM" in labels[1] and "CNAM" in labels[2]
        assert labels[3] != labels[4] and "NNAM" in labels[3] and "NNAM" in labels[4]
        objective = _select_record_field(editor, qapp, "Objective two")
        assert objective != target
        view.translation.setPlainText("Objective draft")

        QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(1).center())
        assert view.browsing_mode == "dialogue"
        assert editor._selected_key == objective
        assert view.translation.toPlainText() == "Objective draft"
        QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(0).center())
        assert view.browsing_mode == "record"
        assert editor._selected_key == objective
        assert view.record_table.currentIndex().row() == 4
        assert view.translation.toPlainText() == "Objective draft"
        _select_record_field(editor, qapp, "Journal two")
        assert view.translation.toPlainText() == ""
        _select_record_field(editor, qapp, "Objective two")
        assert view.translation.toPlainText() == "Objective draft"
        assert tuple(ProjectStore(database).iter_entries(variant)) == before


def test_late_record_load_cannot_replace_newly_opened_record(tmp_path, qapp, monkeypatch):
    from transbridge.ui.dialogue import record_browser

    with _reopened_editor(tmp_path, qapp) as (editor, _services, database, variant):
        rows = tuple(ProjectStore(database).iter_entries(variant))
        book = next(row.state.entry_key for row in rows if row.snapshot.original == "Book text")
        effect = next(row.state.entry_key for row in rows if row.snapshot.original == "Effect description")
        started, release = Event(), Event()
        find_entries = record_browser.find_record_entries

        def delayed_lookup(collection, selected):
            if selected.identity == book:
                started.set()
                assert release.wait(5), "The delayed record worker was not released"
            return find_entries(collection, selected)

        monkeypatch.setattr(record_browser, "find_record_entries", delayed_lookup)
        try:
            editor.open_entry(book)
            paged._wait_until(qapp, started.is_set)
            editor.open_entry(effect)
            assert editor._selected_key == effect
            assert editor.view.original.toPlainText() == "Effect description"
            editor.view.translation.setPlainText("Keep the new record draft")
            delivered = []
            editor.view.record_model.modelReset.connect(
                lambda: delivered.append(tuple(entry.original for entry in editor.view.record_model.entries))
            )
            release.set()
            paged._wait_until(
                qapp,
                lambda: (
                    tuple(entry.original for entry in editor.view.record_model.entries)
                    == ("Effect title", "Effect description")
                ),
            )
            assert editor._selected_key == effect
            assert editor.view.translation.toPlainText() == "Keep the new record draft"
            assert editor.view.record_table.currentIndex().row() == 1
            assert all(not any(text.startswith("Book") for text in entries) for entries in delivered)
        finally:
            release.set()
