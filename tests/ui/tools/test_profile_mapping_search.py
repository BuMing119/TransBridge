from threading import Event, get_ident
from types import SimpleNamespace

from PyQt6.QtCore import Qt, QThreadPool, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QInputDialog, QLineEdit, QMessageBox
import pytest

from tests.ui.tools.terminology.search_test_support import wait_search
from tests.ui.tools.test_terminology_profile_edit_protection import _Context
from tests.ui.workbench.terminology_async_support import wait_profile, wait_until
from transbridge.application.terminology.models import TermScope
from transbridge.application.terminology_profiles import (
    InMemoryTerminologyProfileRepository,
    ProfileTermMapping,
    TerminologyProfileContent,
    TerminologyProfileService,
    logical_term_key,
)
from transbridge.ui.tools.terminology import async_row_filter as filtering
from transbridge.ui.tools.terminology_profiles import TerminologyProfileManagerDialog, manager_dialog
from transbridge.ui.tools.terminology_profiles.mapping_editor import ProfileMappingEditor
from transbridge.ui.workbench.terminology_profile_bar import TerminologyProfileBar
from transbridge.ui.workbench.terminology_profile_controller import TerminologyProfileUiController

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def manager(monkeypatch):
    service = TerminologyProfileService(InMemoryTerminologyProfileRepository())
    mappings = (
        ProfileTermMapping("Dragon", "项目龙", "龙"),
        ProfileTermMapping("Dragon", "插件甲龙", "龙", "plugin", "A.esp"),
        ProfileTermMapping("Dragon", "插件乙龙", "龙", "plugin", "B.esp"),
        *(ProfileTermMapping(f"Term {i:04}", f"译名 {i:04}") for i in range(300)),
    )
    profile = service.create_with_content("project-a", "Scheme", TerminologyProfileContent(mappings))
    dialog = TerminologyProfileManagerDialog(service, "project-a", selected_profile_id=profile.profile_id)
    monkeypatch.setattr(manager_dialog, "choose_unsaved_action", lambda *_args: QMessageBox.StandardButton.Discard)
    yield dialog, service, profile
    dialog.close()
    _APP.processEvents()


def test_return_in_scheme_search_does_not_trigger_any_creation_dialog(manager, monkeypatch):
    dialog, _service, _profile = manager
    prompts = []
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: (prompts.append(args[1]) or "", False))
    dialog.show()
    _APP.processEvents()
    dialog.mapping_search.setFocus()
    dialog.mapping_search.setText("Dragon")
    QTest.keyClick(dialog.mapping_search, Qt.Key.Key_Return)
    wait_search(dialog.mapping_table.filter_model.search)
    assert prompts == []
    assert dialog.mapping_table.model().rowCount() == 3


@pytest.mark.parametrize("scope, expected", [(TermScope.project(), "项目龙"), (TermScope.plugin("B.esp"), "插件乙龙")])
def test_open_intent_locates_exact_scope_after_background_search(manager, scope, expected):
    dialog, _service, _profile = manager
    dialog.open_intent(original="Dragon", scope=scope)
    wait_search(dialog.mapping_table.filter_model.search)
    assert dialog.mapping_table.currentIndex().data() == expected


@pytest.mark.parametrize("scope", [None, TermScope.plugin("Missing.esp")])
def test_missing_or_ambiguous_scope_does_not_guess_another_mapping(manager, scope):
    dialog, _service, _profile = manager
    dialog.open_intent(original="Dragon", scope=TermScope.project())
    wait_search(dialog.mapping_table.filter_model.search)
    dialog.open_intent(original="Dragon", scope=scope)
    wait_search(dialog.mapping_table.filter_model.search)
    assert not dialog.mapping_table.currentIndex().isValid()
    assert "多个使用范围" in dialog.status_label.text() if scope is None else "没有" in dialog.status_label.text()


def test_controller_passes_scope_to_both_new_and_existing_manager(manager):
    _dialog, service, profile = manager
    service.publish(profile.profile_id, expected_draft_revision=0)
    service.select("project-a", "variant-a", profile.profile_id)
    bar = TerminologyProfileBar()
    controller = TerminologyProfileUiController(
        _Context(),
        SimpleNamespace(profile_service_for=lambda _project: service),
        bar,
        SimpleNamespace(set_terminology_profile=lambda *_args, **_kwargs: None),
    )
    wait_profile(controller)
    try:
        controller.open_manager(original="Dragon", scope=TermScope.plugin("B.esp"))
        opened = controller._manager
        wait_search(opened.mapping_table.filter_model.search)
        assert opened.mapping_table.currentIndex().data() == "插件乙龙"
        controller.open_manager(original="Dragon", scope=TermScope.plugin("A.esp"))
        assert controller._manager is opened
        wait_search(opened.mapping_table.filter_model.search)
        assert opened.mapping_table.currentIndex().data() == "插件甲龙"
    finally:
        controller._manager.close()
        bar.close()
        _APP.processEvents()


@pytest.mark.parametrize(
    "requested, stored",
    [
        ("Dragon  Born", "Dragon Born"),
        ("Ｄｒａｇｏｎ　Ｂｏｒｎ", "Dragon Born"),
        ("Dragon Born", "Ｄｒａｇｏｎ　Ｂｏｒｎ"),
    ],
)
def test_controller_locates_normalized_identity_without_rewriting_search_or_guessing_scope(requested, stored):
    service = TerminologyProfileService(InMemoryTerminologyProfileRepository())
    target = ProfileTermMapping(stored, "指定插件译名", scope_kind="plugin", plugin_id="B.esp")
    assert logical_term_key(requested, scope_kind="plugin", plugin_id="B.esp") == target.term_key
    profile = service.create_with_content(
        "project-a",
        "Scheme",
        TerminologyProfileContent((
            ProfileTermMapping(stored, "项目译名"),
            ProfileTermMapping(stored, "另一插件译名", scope_kind="plugin", plugin_id="A.esp"),
            target,
            *(ProfileTermMapping(f"Term {i:04}", f"译名 {i:04}") for i in range(300)),
        )),
    )
    service.publish(profile.profile_id, expected_draft_revision=0)
    service.select("project-a", "variant-a", profile.profile_id)
    bar = TerminologyProfileBar()
    controller = TerminologyProfileUiController(
        _Context(),
        SimpleNamespace(profile_service_for=lambda _project: service),
        bar,
        SimpleNamespace(set_terminology_profile=lambda *_args, **_kwargs: None),
    )
    wait_profile(controller)
    try:
        controller.open_manager(original=requested, scope=TermScope.plugin("B.esp"))
        opened = controller._manager
        wait_search(opened.mapping_table.filter_model.search)
        assert opened.mapping_search.text() == requested
        assert opened.mapping_table.currentIndex().data() == "指定插件译名"
        assert opened.mapping_table.currentIndex().siblingAtColumn(0).data() == stored
        assert not opened.mapping_table.is_dirty
        controller.open_manager(original=requested, scope=TermScope.plugin("Missing.esp"))
        wait_search(opened.mapping_table.filter_model.search)
        assert opened.mapping_search.text() == requested
        assert not opened.mapping_table.currentIndex().isValid()
        assert "没有" in opened.status_label.text()
    finally:
        controller._manager.close()
        bar.close()
        _APP.processEvents()


def test_fifty_thousand_mapping_search_indexes_once_off_thread_and_keeps_qt_responsive(monkeypatch):
    pool = QThreadPool()
    editor = ProfileMappingEditor(thread_pool=pool)
    main_thread = get_ident()
    index_threads, query_threads = [], []
    entered, release = Event(), Event()
    original_index, original_match = filtering.build_search_index, filtering.match_search_index

    def index(*args):
        index_threads.append(get_ident())
        return original_index(*args)

    def match(*args):
        query_threads.append(get_ident())
        entered.set()
        assert release.wait(3)
        return original_match(*args)

    monkeypatch.setattr(filtering, "build_search_index", index)
    monkeypatch.setattr(filtering, "match_search_index", match)
    try:
        editor.load(tuple(ProfileTermMapping(f"Term {i:05}", f"译名 {i:05}") for i in range(50000)))
        wait_until(lambda: editor.filter_model.search._index is not None)
        editor.search("Term 49999")
        wait_until(entered.is_set)
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        _APP.processEvents()
        assert ticks == [True]
        assert editor.filter_model.search.is_pending
        release.set()
        wait_search(editor.filter_model.search)
        assert editor.model().rowCount() == 1
        assert editor.model().index(0, 0).data() == "Term 49999"
        editor.search("Term 49998")
        wait_search(editor.filter_model.search)
        assert editor.model().index(0, 0).data() == "Term 49998"
        assert len(index_threads) == 1
        assert all(thread != main_thread for thread in (*index_threads, *query_threads))
    finally:
        release.set()
        editor.close()
        assert pool.waitForDone(3000)


@pytest.mark.parametrize("change", ["query", "load", "edit", "delete"])
def test_late_search_result_cannot_replace_new_query_or_source_rows(change):
    pool = QThreadPool()
    editor = ProfileMappingEditor(thread_pool=pool)
    search = editor.filter_model.search
    search._small_limit = 0
    try:
        editor.load((ProfileTermMapping("Alpha", "甲"), ProfileTermMapping("Beta", "乙")))
        wait_until(lambda: search._index is not None)
        editor.search("Alpha")
        search._timer.stop()
        search._search()
        assert pool.waitForDone(3000)  # Leave the old result queued until after the new state exists.
        if change == "query":
            editor.search("Beta")
        elif change == "load":
            editor.load((ProfileTermMapping("Other", "丙"),))
        elif change == "edit":
            editor.mapping_model.setData(editor.mapping_model.index(0, 0), "Other")
        else:
            editor.mapping_model.removeRow(0)
        wait_search(search)
        assert editor.model().rowCount() == (1 if change == "query" else 0)
        if change == "query":
            assert editor.model().index(0, 0).data() == "Beta"
    finally:
        editor.close()
        assert pool.waitForDone(3000)


def test_filtered_edit_delete_and_insert_keep_source_row_identity(monkeypatch):
    editor = ProfileMappingEditor()
    try:
        editor.load(tuple(ProfileTermMapping(f"Term {i:03}", f"译名 {i:03}") for i in range(300)))
        editor.search("Term 299")
        wait_search(editor.filter_model.search)
        assert editor.model().setData(editor.model().index(0, 2), "改后的译名")
        wait_search(editor.filter_model.search)
        assert editor.mapping_model.index(299, 2).data() == "改后的译名"
        editor.selectRow(0)
        monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
        editor.remove_selected()
        wait_search(editor.filter_model.search)
        assert editor.mapping_model.rowCount() == 299
        assert editor.model().rowCount() == 0
        editor.append_mapping(ProfileTermMapping("Term 299", "新增译名"))
        wait_search(editor.filter_model.search)
        assert editor.model().index(0, 2).data() == "新增译名"
    finally:
        editor.close()


def test_search_commits_active_input_and_rejected_close_keeps_search_working(manager, monkeypatch):
    dialog, _service, _profile = manager
    dialog.show()
    _APP.processEvents()
    editor = dialog.mapping_table
    index = editor.model().index(0, 2)
    editor.edit(index)
    _APP.processEvents()
    text = editor.findChild(QLineEdit)
    text.setText("尚未失焦的输入")
    dialog.mapping_search.setText("尚未失焦的输入")
    wait_search(editor.filter_model.search)
    assert editor.model().index(0, 2).data() == "尚未失焦的输入"
    monkeypatch.setattr(manager_dialog, "choose_unsaved_action", lambda *_args: QMessageBox.StandardButton.Cancel)
    dialog.close()
    assert dialog.isVisible()
    assert not editor.filter_model.search._jobs.closed.is_set()
    dialog.mapping_search.setText("Term 0299")
    wait_search(editor.filter_model.search)
    assert editor.model().index(0, 0).data() == "Term 0299"
    monkeypatch.setattr(manager_dialog, "choose_unsaved_action", lambda *_args: QMessageBox.StandardButton.Discard)


def test_editor_close_rejects_already_queued_search_result():
    pool = QThreadPool()
    editor = ProfileMappingEditor(thread_pool=pool)
    search = editor.filter_model.search
    search._small_limit = 0
    delivered = []
    search.matches_changed.connect(delivered.append)
    editor.load((ProfileTermMapping("Alpha", "甲"), ProfileTermMapping("Beta", "乙")))
    wait_until(lambda: search._index is not None)
    search.set_query("Alpha", debounce=False)
    assert pool.waitForDone(3000)
    before = len(delivered)
    editor.close()
    _APP.processEvents()
    assert len(delivered) == before
    assert search._jobs.closed.is_set()
