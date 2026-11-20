"""The first real double-click uses cached project context before full scans finish."""

from contextlib import closing, contextmanager
from threading import Event, get_ident

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QTextCursor
from PyQt6.QtTest import QTest
import pytest

from tests.dialogue_catalog_support import field, plugin_bytes, record
from tests.dialogue_order_support import dialogue_bytes
from tests.integration.gui import test_project_paged_workbench as paged
from tests.ui.test_dialogue_editor import drain
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.persistence.project_store.paged_reads import WORKBENCH_PAGE_SIZE
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.ui.dialogue.controller import DialogueEditorController
from transbridge.ui.project_rows import ProjectRowsCollection
from transbridge.ui.workbench.translation_table_columns import COL_ORIGINAL

qapp = paged.qapp


@contextmanager
def reopened_dialogue(tmp_path, app):
    root = tmp_path / "data"
    source = tmp_path / "Dialogue.esp"
    fillers = plugin_bytes({
        "NPC_": b"".join(
            record("NPC_", 0x900 + index, field("FULL", f"Filler {index}".encode() + b"\0"))
            for index in range(WORKBENCH_PAGE_SIZE + 10)
        )
    })
    source.write_bytes(dialogue_bytes() + fillers[len(plugin_bytes({})) :])
    with closing(paged._services(root)) as services:
        created = services.gui_project_commands.create_project(
            ProjectProvisioningRequest("First context", source=ProjectSourceRequest(str(source))), paged._CONTEXT
        )
        assert created.is_success, created.diagnostics
        database = services.projects.path_for(services.project_lifecycle.active.project_ref)
    source.unlink()
    with paged._workbench(root, app) as (host, coordinator, services):
        failures = paged._begin_open(host, coordinator, database)
        paged._wait_until(app, lambda: host.project_open_worker is None)
        assert not failures, failures
        preview = host.workbench.preview
        assert isinstance(host.context.collection, ProjectRowsCollection)
        editor = DialogueEditorController(
            host.context, host.workbench, preview, [], projection=services.project_projection
        )
        try:
            yield editor, preview
        finally:
            drain(editor)
            editor.close()


def double_click(preview, original):
    table = preview._table
    row = next(
        row
        for row in range(min(WORKBENCH_PAGE_SIZE, table.rowCount()))
        if table.entry_at_row(row) is not None and table.entry_at_row(row).original == original
    )
    index = table.model().index(row, COL_ORIGINAL)
    table.scrollTo(index)
    point = table.visualRect(index).center()
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    return table.entry_at_row(row).identity


@pytest.mark.parametrize("original", ["Fixture Quest", "First topic", "First reply"])
def test_cached_task_context_opens_before_resident_and_dialogue_scans_finish(qapp, tmp_path, monkeypatch, original):
    main_thread = get_ident()
    started, release = Event(), Event()
    scans = []
    iterate = ProjectStore.iter_entries

    def gated_scan(*args, **kwargs):
        scans.append(get_ident())
        assert get_ident() != main_thread, "A full project scan ran on the GUI thread"
        started.set()
        assert release.wait(10)
        yield from iterate(*args, **kwargs)

    monkeypatch.setattr(ProjectStore, "iter_entries", gated_scan)
    try:
        with reopened_dialogue(tmp_path, qapp) as (editor, preview):
            try:
                paged._wait_until(qapp, started.is_set)
                assert not preview._paging.resident_ready
                monkeypatch.setattr(
                    preview,
                    "editor_navigation_keys",
                    lambda: pytest.fail("Task activation materialized the full filtered navigation"),
                )
                target = double_click(preview, original)
                assert editor.dialog.isVisible()
                assert editor._selected_key == target
                if original == "Fixture Quest":
                    assert editor.view.browsing_mode == "record"
                    editor.view.browse_tabs.setCurrentIndex(1)
                assert editor.view.context_panel.isVisible() and editor.view.context_panel.isEnabled()
                assert editor.view.quest_combo.count() >= 1
                assert editor.view.tree_model.rowCount() >= 2
                assert "补全" in editor.view.context_reason.text()
                assert len(editor._navigation_keys) > 1
                editor.move(1 if editor.view.next_button.isEnabled() else -1)
                assert editor._selected_key != target
                editor._display_entry(target)
                field = editor.view.translation
                QTest.keyClicks(field, "pending draft")
                cursor = field.textCursor()
                cursor.setPosition(2)
                cursor.setPosition(7, QTextCursor.MoveMode.KeepAnchor)
                field.setTextCursor(cursor)
                heartbeat = []
                QTimer.singleShot(0, lambda: heartbeat.append(True))
                qapp.processEvents()
                assert heartbeat
                assert field.hasFocus()
            finally:
                release.set()
                drain(editor)
            paged._wait_until(qapp, lambda: preview._paging.resident_ready)
            assert editor._selected_key == target
            assert field.toPlainText() == "pending draft"
            assert field.textCursor().anchor() == 2 and field.textCursor().position() == 7
            assert not editor.view.context_reason.isVisible()
            field.undo()
            assert field.toPlainText() == ""
            assert scans and all(thread != main_thread for thread in scans)
    finally:
        release.set()


def test_ordinary_editor_waits_for_complete_opening_filter_navigation(qapp, tmp_path, monkeypatch):
    started, release = Event(), Event()
    iterate = ProjectStore.iter_entries

    def gated_scan(*args, **kwargs):
        started.set()
        assert release.wait(10)
        yield from iterate(*args, **kwargs)

    monkeypatch.setattr(ProjectStore, "iter_entries", gated_scan)
    try:
        with reopened_dialogue(tmp_path, qapp) as (editor, preview):
            try:
                paged._wait_until(qapp, started.is_set)
                target = double_click(preview, "Book")
                assert not editor.dialog.isVisible()
            finally:
                release.set()
                drain(editor)
            paged._wait_until(qapp, lambda: preview._paging.resident_ready)
            paged._wait_until(qapp, editor.dialog.isVisible)
            assert editor._selected_key == target
            assert not editor.view.context_panel.isEnabled()
            assert len(editor._navigation_keys) == len(editor.context.collection)
            assert editor._navigation_keys == tuple(entry.identity for entry in preview.filtered_entries())
    finally:
        release.set()


def test_pending_ordinary_double_click_is_dropped_when_filter_changes(qapp, tmp_path, monkeypatch):
    started, release = Event(), Event()
    iterate = ProjectStore.iter_entries

    def gated_scan(*args, **kwargs):
        started.set()
        assert release.wait(10)
        yield from iterate(*args, **kwargs)

    monkeypatch.setattr(ProjectStore, "iter_entries", gated_scan)
    try:
        with reopened_dialogue(tmp_path, qapp) as (editor, preview):
            try:
                paged._wait_until(qapp, started.is_set)
                double_click(preview, "Book")
                assert not editor.dialog.isVisible()
                QTest.keyClicks(preview._filters_view.search_all, "Filler")
            finally:
                release.set()
                drain(editor)
            paged._wait_until(qapp, lambda: preview._paging._result_key == preview._paging._query_key())
            assert not editor.dialog.isVisible()
            assert editor._current is None and editor._selected_key is None
            assert preview.filtered_entries()
            assert all("Filler" in entry.original for entry in preview.filtered_entries())
    finally:
        release.set()


def test_filtered_task_keeps_cached_context_and_draft_while_full_dialogue_rows_load(qapp, tmp_path, monkeypatch):
    started, release = Event(), Event()
    iterate = ProjectStore.iter_entries
    with reopened_dialogue(tmp_path, qapp) as (editor, preview):
        paged._wait_until(qapp, lambda: preview._paging.resident_ready)
        QTest.keyClicks(preview._filters_view.search_all, "First reply")
        paged._wait_until(qapp, lambda: preview._paging._result_key == preview._paging._query_key())
        monkeypatch.setattr(
            preview,
            "editor_navigation_keys",
            lambda: pytest.fail("Task activation materialized the full filtered navigation"),
        )

        def gated_scan(*args, **kwargs):
            started.set()
            assert release.wait(10)
            yield from iterate(*args, **kwargs)

        monkeypatch.setattr(ProjectStore, "iter_entries", gated_scan)
        try:
            target = double_click(preview, "First reply")
            paged._wait_until(qapp, started.is_set)
            assert editor.view.context_panel.isEnabled()
            assert editor.view.table_model.rowCount() == 3
            assert len(editor._navigation_keys) > len(preview.filtered_entries()) == 1
            editor.view.translation.setPlainText("filtered draft")
        finally:
            release.set()
            drain(editor)
        assert editor._selected_key == target
        assert editor.view.translation.toPlainText() == "filtered draft"
        assert preview.get_filter_state()["search_all"] == "First reply"
        assert len(preview.filtered_entries()) == 1
        editor.discard()
