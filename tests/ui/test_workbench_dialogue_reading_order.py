"""Default Workbench ranks remain a display projection of captured plugin data."""

from contextlib import closing
from dataclasses import replace
import os
from pathlib import Path
from threading import Event, get_ident
from time import monotonic
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit
import pytest

from tests.dialogue_catalog_support import field, plugin_bytes, record
from tests.dialogue_order_support import dialogue_bytes
from tests.integration.gui.test_project_paged_workbench import _CONTEXT, _services, _wait_until, _workbench
from tests.ui.fixtures.current_editing import CurrentEditingProject
from transbridge.application.io import FormatId, SourceDescriptor, SourceSnapshot
from transbridge.application.io.dialogue_reading_view import ReadingEntry
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.parser.plugin_parser import PluginParser
from transbridge.persistence.project_store.paged_reads import WORKBENCH_PAGE_SIZE
from transbridge.ui import context as context_module
from transbridge.ui.project_labels import entry_label_key
from transbridge.ui.workbench import dialogue_reading_order as reading
from transbridge.ui.workbench.step2 import Step2PreviewWidget
from transbridge.ui.workbench.translation_table_columns import COL_INDEX, COL_KEY, COL_TRANSLATION

_APP = QApplication.instance() or QApplication([])
_READING_TEXTS = ["Fixture Quest", "Book", "First topic", "First reply", "Second reply", "Later topic", "Later reply"]


def wait_for(predicate):
    deadline = monotonic() + 5
    while not predicate() and monotonic() < deadline:
        _APP.processEvents()
        QTest.qWait(1)
    assert predicate()


def texts(view):
    return [view._table.entry_at_row(row).original for row in range(view._table.rowCount())]


def numbers(view):
    return [view._table.model().index(row, COL_INDEX).data() for row in range(view._table.rowCount())]


def test_paged_project_reopens_in_dialogue_reading_order_without_source(tmp_path, monkeypatch):
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token=""))
    root = tmp_path / "data"
    plugin = tmp_path / "Dialogue.esp"
    fillers = [f"Filler {index}" for index in range(WORKBENCH_PAGE_SIZE - 4)]
    filler_plugin = plugin_bytes({
        "NPC_": b"".join(
            record("NPC_", 0x900 + index, field("FULL", text.encode() + b"\0")) for index, text in enumerate(fillers)
        ),
    })
    header_length = len(plugin_bytes({}))
    plugin.write_bytes(filler_plugin + dialogue_bytes()[header_length:])
    expected = [*fillers, *_READING_TEXTS]
    with closing(_services(root)) as services:
        created = services.gui_project_commands.create_project(
            ProjectProvisioningRequest("Paged dialogue", source=ProjectSourceRequest(str(plugin))), _CONTEXT
        )
        assert created.is_success, created.diagnostics
        active = services.project_lifecycle.active
        database = services.projects.path_for(active.project_ref)
        identity = active.project_ref.identity.value, active.formal_variant_ref.identity.value
    plugin.unlink()

    with _workbench(root, _APP) as (host, coordinator, _services_for_view):
        coordinator.open_project_path(str(database))
        _wait_until(_APP, lambda: host.project_open_worker is None)

        assert host.context.active_version_identity == identity
        preview = host.workbench.preview
        table = preview._table
        assert table.rowCount() == len(expected)
        first_page = [table.entry_at_row(row).original for row in range(WORKBENCH_PAGE_SIZE)]
        assert first_page == expected[:WORKBENCH_PAGE_SIZE]
        scrollbar = table.verticalScrollBar()
        _wait_until(_APP, lambda: scrollbar.maximum() > 0)
        scrollbar.setValue(scrollbar.maximum())
        tail = range(len(fillers), len(expected))
        _wait_until(_APP, lambda: all(table.entry_at_row(row) is not None for row in tail))
        assert table.rowCount() == len(expected)
        assert [table.entry_at_row(row).original for row in tail] == _READING_TEXTS


@pytest.fixture
def make_view(monkeypatch):
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token=""))
    owned = []

    def create(*, start=True, prepared=False):
        content = dialogue_bytes()
        parser = PluginParser()
        entries = tuple(
            parser.parse_plugin(
                Path("missing.esp"),
                captured_content=content,
                discover_sibling_strings=False,
                skip_empty=False,
                raise_on_error=True,
            )
        )
        snapshot = SourceSnapshot.from_bytes(SourceDescriptor("Z:/gone/missing.esp"), FormatId.PLUGIN_SSE, content)
        options = {"source_snapshot": snapshot}
        if prepared:
            options["reading_source_entries"] = tuple(
                ReadingEntry(entry.identity, entry.context, entry.metadata) for entry in entries
            )
        project = CurrentEditingProject(entries, **options)
        view = Step2PreviewWidget(project.context)
        view.resize(1000, 500)
        view.show()
        owned.append((view, project))
        if start:
            view.refresh(project.context.collection)
        return view, project, entries

    yield create
    for view, project in owned:
        if not sip.isdeleted(view):
            view.close()
            view.deleteLater()
        project.close()
    _APP.processEvents()


def test_background_default_filter_and_manual_reset_preserve_all_entry_fields(make_view, monkeypatch):
    main_thread, calls = get_ident(), []
    actual = reading.dialogue_order_from_snapshot

    def capture(snapshot, **kwargs):
        calls.append(get_ident())
        return actual(snapshot, **kwargs)

    monkeypatch.setattr(reading, "dialogue_order_from_snapshot", capture)
    view, project, entries = make_view()
    before = [entry.to_dict() for entry in entries]
    wait_for(lambda: texts(view) == _READING_TEXTS)
    assert calls and all(thread != main_thread for thread in calls)
    assert tuple(project.context.collection) == entries
    assert [entry.to_dict() for entry in entries] == before
    assert numbers(view) == [str(index) for index in range(1, 8)]

    view.apply_filter_state({"search_orig": "reply"})
    assert texts(view) == ["First reply", "Second reply", "Later reply"]
    assert numbers(view) == ["4", "5", "7"]
    assert view.editor_navigation_keys() == tuple(view._table.entry_at_row(row).identity for row in range(3))
    header = view._table.horizontalHeader()
    header.sectionClicked.emit(COL_KEY)
    assert [view._table.entry_at_row(row).key for row in range(3)] == sorted(
        entry.key for entry in entries if "reply" in entry.original
    )
    header.sectionClicked.emit(COL_KEY)
    header.sectionClicked.emit(COL_KEY)
    assert texts(view) == ["First reply", "Second reply", "Later reply"]
    assert [entry.to_dict() for entry in entries] == before


def test_late_rank_keeps_selected_identity_and_manual_sort_intent(make_view, monkeypatch):
    started, release = Event(), Event()
    actual = reading.build_dialogue_reading_view

    def blocked(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return actual(*args, **kwargs)

    monkeypatch.setattr(reading, "build_dialogue_reading_view", blocked)
    view, _project, entries = make_view()
    try:
        wait_for(started.is_set)
        wanted = next(entry for entry in entries if entry.original == "Later topic")
        view._table.locate_entry(entry_label_key(wanted))
        view._table.horizontalHeader().sectionClicked.emit(COL_KEY)
        release.set()
        wait_for(lambda: bool(view._dialogue_reading_order._completed_keys))
        assert [view._table.entry_at_row(row).key for row in range(7)] == sorted(entry.key for entry in entries)
        assert view._table.selected_entry_ids() == (entry_label_key(wanted),)
        assert view._table.entry_at_row(view._table.currentIndex().row()).identity == wanted.identity
    finally:
        release.set()


def test_late_rank_waits_for_uncommitted_f2_draft_and_edit_keeps_identity(make_view, monkeypatch):
    started, release = Event(), Event()
    actual = reading.build_dialogue_reading_view

    def blocked(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return actual(*args, **kwargs)

    monkeypatch.setattr(reading, "build_dialogue_reading_view", blocked)
    view, project, entries = make_view()
    try:
        wait_for(started.is_set)
        wanted = next(entry for entry in entries if entry.original == "Later topic")
        row = next(row for row in range(7) if view._table.entry_at_row(row).identity == wanted.identity)
        index = view._table.model().index(row, COL_TRANSLATION)
        view._table.setCurrentIndex(index)
        view._table.edit(index)
        _APP.processEvents()
        editor = next(editor for editor in view._table.findChildren(QLineEdit) if editor.isVisible())
        editor.setText("uncommitted draft")
        physical = texts(view)
        release.set()
        wait_for(lambda: view._dialogue_reading_order._pending is not None)
        assert editor.isVisible() and editor.text() == "uncommitted draft"
        assert texts(view) == physical
        QTest.keyClick(editor, Qt.Key.Key_Return)
        wait_for(lambda: texts(view) == _READING_TEXTS)
        current = project.context.collection.get(wanted.identity)
        assert current.translation == "uncommitted draft"
        assert all(entry.translation == "" for entry in project.context.collection if entry.identity != wanted.identity)
        visible = next(
            view._table.entry_at_row(row)
            for row in range(7)
            if view._table.entry_at_row(row).identity == wanted.identity
        )
        assert visible.translation == "uncommitted draft"
    finally:
        release.set()


def test_translation_edit_reuses_reading_ranks_with_current_entry_objects(make_view, monkeypatch):
    view, project, entries = make_view()
    wait_for(lambda: texts(view) == _READING_TEXTS)

    calls = []
    actual = reading.build_dialogue_reading_view

    def counted(*args, **kwargs):
        calls.append(True)
        return actual(*args, **kwargs)

    monkeypatch.setattr(reading, "build_dialogue_reading_view", counted)
    wanted = next(entry for entry in entries if entry.original == "Later reply")
    replaced = tuple(replace(entry, translation="Edited", stage=3) if entry is wanted else entry for entry in entries)
    project.context.collection = TranslationEntryCollection(replaced)
    _APP.processEvents()
    assert texts(view) == _READING_TEXTS
    visible = next(
        view._table.entry_at_row(row) for row in range(7) if view._table.entry_at_row(row).identity == wanted.identity
    )
    assert visible.translation == "Edited" and visible.stage == 3
    assert calls == []


def test_full_source_rank_mapping_and_indices_are_prepared_in_worker(make_view, monkeypatch):
    main_thread, prepared = get_ident(), []
    actual = reading._PreparedOrder

    def capture(*args, **kwargs):
        result = actual(*args, **kwargs)
        prepared.append((get_ident(), result))
        return result

    monkeypatch.setattr(reading, "_PreparedOrder", capture)
    view, _project, entries = make_view(prepared=True)
    wait_for(lambda: texts(view) == _READING_TEXTS)
    assert prepared and prepared[0][0] != main_thread
    result = prepared[0][1]
    assert result.source_keys == tuple(entry.identity for entry in entries)
    assert tuple(result.source_keys[row] for row in result.source_rows) == result.ordered_keys
    assert view._table._sorting.default_ranks is result.ranks
    assert view._table._display_order is result.source_rows
    with pytest.raises(TypeError):
        result.ranks[entries[0].identity] = 99


def test_source_switch_discards_a_late_worker_result(make_view, monkeypatch):
    started, release, finished = Event(), Event(), Event()
    actual = reading.build_dialogue_reading_view

    def blocked(*args, **kwargs):
        started.set()
        assert release.wait(5)
        try:
            return actual(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(reading, "build_dialogue_reading_view", blocked)
    view, project, _entries = make_view()
    try:
        wait_for(started.is_set)
        other = TranslationEntry("other", "other", "Other source", "", 0, "NPC_:FULL")
        project.context.add_slot("other", context_module.CollectionSlot("Other", TranslationEntryCollection((other,))))
        release.set()
        wait_for(finished.is_set)
        _APP.processEvents()
        assert texts(view) == ["Other source"]
        assert view._dialogue_reading_order._completed_keys == ()
    finally:
        release.set()


def test_destroyed_preview_cancels_worker_before_result_delivery(make_view, monkeypatch):
    started, release, finished = Event(), Event(), Event()
    actual = reading.build_dialogue_reading_view

    def blocked(*args, **kwargs):
        started.set()
        assert release.wait(5)
        try:
            return actual(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(reading, "build_dialogue_reading_view", blocked)
    view, _project, _entries = make_view()
    try:
        wait_for(started.is_set)
        closed = view._dialogue_reading_order._closed
        sip.delete(view)
        assert closed.is_set()
        release.set()
        wait_for(finished.is_set)
        _APP.processEvents()
    finally:
        release.set()


def test_changed_structure_failure_explicitly_returns_to_source_display(make_view, monkeypatch, caplog):
    view, project, entries = make_view()
    wait_for(lambda: texts(view) == _READING_TEXTS)

    def fail(*_args, **_kwargs):
        raise ValueError("Invalid changed relationship structure")

    monkeypatch.setattr(reading, "build_dialogue_reading_view", fail)
    changed = tuple(replace(entry, metadata=(*entry.metadata, ("changed", True))) for entry in entries)
    project.context.collection = TranslationEntryCollection(changed)
    wait_for(lambda: "WORKBENCH_DIALOGUE_ORDER_FAILED" in caplog.text)
    assert texts(view) == [entry.original for entry in changed]
    assert numbers(view) == [str(index) for index in range(1, 8)]


def test_capture_yields_between_small_immutable_batches(make_view):
    class HoldingPool:
        calls = []

        def start(self, call):
            self.calls.append(call)

    view, _project, entries = make_view(start=False)
    coordinator = view._dialogue_reading_order
    pool = HoldingPool()
    coordinator._pool, coordinator._batch_size = pool, 2
    heartbeats = []
    view.refresh(view._ctx.collection)
    QTimer.singleShot(0, lambda: heartbeats.append(coordinator._offset))
    wait_for(lambda: bool(pool.calls))
    assert heartbeats and heartbeats[0] < len(entries)
    assert all(isinstance(entry, ReadingEntry) for entry in pool.calls[0].entries)
    assert len(pool.calls[0].entries) == len(entries)


def test_complete_prepared_source_ranks_survive_removed_visible_entries(make_view):
    view, project, entries = make_view(prepared=True)
    wait_for(lambda: texts(view) == _READING_TEXTS)
    source_inputs = project.context.active_slot.reading_source_entries
    remaining = tuple(entry for entry in entries if entry.original != "First topic")
    project.context.collection = TranslationEntryCollection(remaining)
    _APP.processEvents()
    assert project.context.active_slot.reading_source_entries is source_inputs
    assert texts(view) == [text for text in _READING_TEXTS if text != "First topic"]
    assert numbers(view) == ["1", "2", "4", "5", "6", "7"]


def test_returning_to_cached_source_restores_its_default_without_new_layout(make_view, monkeypatch):
    view, project, _entries = make_view(prepared=True)
    wait_for(lambda: texts(view) == _READING_TEXTS)
    original_key = project.context.active_key
    calls = []
    actual = reading.build_dialogue_reading_view

    def counted(*args, **kwargs):
        calls.append(True)
        return actual(*args, **kwargs)

    monkeypatch.setattr(reading, "build_dialogue_reading_view", counted)
    other = TranslationEntry("other", "other", "Other source", "", 0, "NPC_:FULL")
    project.context.add_slot("other", context_module.CollectionSlot("Other", TranslationEntryCollection((other,))))
    assert texts(view) == ["Other source"]
    project.context.activate_slot(original_key)
    assert texts(view) == _READING_TEXTS
    assert numbers(view) == [str(index) for index in range(1, 8)]
    assert calls == []
