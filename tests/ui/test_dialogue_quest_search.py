"""Searchable quest navigation commits records only after an explicit choice."""

from dataclasses import replace
from threading import Event

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from tests.dialogue_support import dialogue_entries
from tests.ui import (
    test_dialogue_editor as editor_tests,
    test_dialogue_lazy_index as lazy_tests,
    test_dialogue_paged_first_use as paged_tests,
    test_dialogue_scenes as scene_tests,
)
from transbridge.application.dialogue import loading
from transbridge.application.dialogue.index import DialogueIndex, DialogueQuest, DialogueTopic
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.ui.dialogue.quest_picker import QuestPicker

editor = editor_tests.editor
scene_editor = scene_tests.scene_editor
lazy_editor = lazy_tests.lazy_editor
qapp = paged_tests.qapp
drain = editor_tests.drain


def search(picker, text):
    picker.search_input.selectAll()
    QTest.keyClicks(picker.search_input, text)
    QApplication.processEvents()


def result_rows(picker):
    model = picker.results.model()
    return [model.index(row, 0) for row in range(model.rowCount())]


def click_result(picker, row):
    index = picker.results.model().index(row, 0)
    picker.results.scrollTo(index)
    QApplication.processEvents()
    QTest.mouseClick(
        picker.results.viewport(), Qt.MouseButton.LeftButton, pos=picker.results.visualRect(index).center()
    )
    QApplication.processEvents()


def sample_index():
    quests = []
    for form_id, name, scene_id in (("00000001", "AlphaQuest", "00000012"), ("00000002", "Name00000010", "00000022")):
        identity = ("fixture.esp", form_id)
        label = f"QUST {{{name}}} [{form_id}]"
        quests.append(
            DialogueQuest(
                identity,
                label,
                (
                    DialogueTopic((*identity, "journal"), label, (), kind="QUST"),
                    DialogueTopic((*identity, "00000010"), "DIAL {SharedTopic} [00000010]", ()),
                    DialogueTopic((*identity, f"SCEN:{scene_id}"), f"SCEN {{StarScene}} [{scene_id}]", (), kind="SCEN"),
                ),
            )
        )
    return DialogueIndex(tuple(quests), {})


@pytest.fixture
def picker():
    widget = QuestPicker()
    widget.set_dialogue_index(sample_index(), 0)
    widget.resize(400, 32)
    widget.show()
    widget.showPopup()
    QApplication.processEvents()
    yield widget
    widget.hidePopup()
    widget.close()
    widget.deleteLater()
    QApplication.processEvents()


def test_empty_query_lists_only_quests_and_opening_does_not_choose(picker):
    chosen = []
    picker.record_chosen.connect(lambda quest, topic: chosen.append((quest, topic)))
    assert [index.data() for index in result_rows(picker)] == [
        "QUST {AlphaQuest} [00000001]",
        "QUST {Name00000010} [00000002]",
    ]
    assert chosen == []


@pytest.mark.parametrize(
    ("query", "kind", "count"),
    [("aLpHaQuEsT", "QUST", 1), ("sHaReDtOp", "DIAL", 2), ("sTaRsCe", "SCEN", 2)],
)
def test_name_substring_search_is_case_insensitive_across_record_kinds(picker, query, kind, count):
    search(picker, query)
    rows = result_rows(picker)
    assert len(rows) == count
    assert all(index.data().startswith(kind) for index in rows)


def test_exact_form_id_precedes_name_matches_and_duplicates_identify_the_parent_quest(picker):
    search(picker, "00000010")
    rows = result_rows(picker)
    assert len(rows) == 3
    assert all(index.data().startswith("DIAL") for index in rows[:2])
    assert rows[2].data().startswith("QUST")
    assert "AlphaQuest" in rows[0].data()
    assert "Name00000010" in rows[1].data()
    assert rows[0].data() != rows[1].data()
    chosen = []
    picker.record_chosen.connect(lambda quest, topic: chosen.append((quest, topic)))
    click_result(picker, 1)
    assert chosen == [(1, 1)]


def test_reordered_index_refresh_keeps_query_and_the_highlighted_record_identity(picker):
    original_index = sample_index()
    picker.set_dialogue_index(original_index, 0)
    search(picker, "SharedTopic")
    picker.results.setCurrentIndex(result_rows(picker)[1])
    original = picker.results.currentIndex().data()
    # Reindexing can change quest offsets while the popup remains open.
    source = picker.results.currentIndex().data(Qt.ItemDataRole.UserRole)
    assert source == (1, 1)
    picker.set_dialogue_index(original_index, 0)
    assert picker.search_input.text() == "SharedTopic"
    assert picker.results.currentIndex().data() == original
    picker.set_dialogue_index(replace(original_index, quests=tuple(reversed(original_index.quests))), 1)
    assert picker.search_input.text() == "SharedTopic"
    assert picker.results.currentIndex().data() == original
    chosen = []
    picker.record_chosen.connect(lambda quest, topic: chosen.append((quest, topic)))
    QTest.keyClick(picker.search_input, Qt.Key.Key_Return)
    assert chosen == [(0, 1)]


def test_refined_query_prefers_an_exact_name_over_the_previous_substring_selection(picker):
    index = sample_index()
    quest = index.quests[0]
    quest = replace(
        quest,
        topics=(
            quest.topics[0],
            DialogueTopic((*quest.identity, "00000009"), "DIAL {SharedTopicExtra} [00000009]", ()),
            quest.topics[1],
        ),
    )
    picker.set_dialogue_index(replace(index, quests=(quest,)), 0)
    search(picker, "Shared")
    picker.results.setCurrentIndex(result_rows(picker)[0])
    assert "SharedTopicExtra" in picker.results.currentIndex().data()
    search(picker, "SharedTopic")
    assert "{SharedTopic}" in picker.results.currentIndex().data()
    chosen = []
    picker.record_chosen.connect(lambda quest, topic: chosen.append((quest, topic)))
    QTest.keyClick(picker.search_input, Qt.Key.Key_Return)
    assert chosen == [(0, 2)]


def test_printable_key_on_closed_picker_starts_search_without_navigating(scene_editor):
    picker = scene_editor.view.quest_combo
    target = scene_editor._selected_key
    node = scene_editor._node_identity
    scene_editor.view.translation.setPlainText("draft before searching")
    picker.setFocus()
    QTest.keyClicks(picker, "Test")
    QApplication.processEvents()
    assert picker.search_input.isVisible()
    assert picker.search_input.text() == "Test"
    assert scene_editor._selected_key == target and scene_editor._node_identity == node
    assert scene_editor.view.translation.toPlainText() == "draft before searching"
    QTest.keyClick(picker.search_input, Qt.Key.Key_Escape)


def test_typing_no_match_enter_and_escape_leave_navigation_and_draft_untouched(scene_editor):
    view = scene_editor.view
    target = scene_editor._selected_key
    node = scene_editor._node_identity
    view.translation.setPlainText("unapplied draft")
    QTest.mouseClick(view.quest_combo, Qt.MouseButton.LeftButton)
    search(view.quest_combo, "TestScene")
    assert scene_editor._selected_key == target and scene_editor._node_identity == node
    assert view.translation.toPlainText() == "unapplied draft"
    search(view.quest_combo, "no such record exists")
    assert result_rows(view.quest_combo) == []
    QTest.keyClick(view.quest_combo.search_input, Qt.Key.Key_Return)
    assert scene_editor._selected_key == target and scene_editor._node_identity == node
    QTest.keyClick(view.quest_combo.search_input, Qt.Key.Key_Escape)
    QApplication.processEvents()
    assert scene_editor.dialog.isVisible()
    assert scene_editor._selected_key == target and scene_editor._node_identity == node
    assert view.translation.toPlainText() == "unapplied draft"
    assert scene_editor.context.collection.get(target).translation == ""


@pytest.mark.parametrize("activate", ["mouse", "keyboard"])
def test_scene_search_choice_navigates_the_real_editor_and_preserves_previous_draft(scene_editor, activate):
    picker = scene_editor.view.quest_combo
    target = scene_editor._selected_key
    scene_editor.view.translation.setPlainText("draft before scene selection")
    QTest.mouseClick(picker, Qt.MouseButton.LeftButton)
    search(picker, "TestScene")
    assert len(result_rows(picker)) == 1
    if activate == "mouse":
        click_result(picker, 0)
    else:
        QTest.keyClick(picker.search_input, Qt.Key.Key_Down)
        QTest.keyClick(picker.search_input, Qt.Key.Key_Return)
    QApplication.processEvents()
    assert scene_editor._node_identity[-1] == "SCEN:00000012"
    assert scene_editor.view.tree.currentIndex().row() == 3
    assert scene_editor.view.table_model.entries == dialogue_entries()[1:]
    scene_editor.open_entry(target)
    assert scene_editor.view.translation.toPlainText() == "draft before scene selection"


def test_slow_catalog_completion_retains_active_search_and_draft(lazy_editor, monkeypatch):
    started, release = Event(), Event()
    read = loading.read_dialogue_catalog

    def gated_catalog(plugin):
        started.set()
        assert release.wait(5)
        return read(plugin)

    monkeypatch.setattr(loading, "read_dialogue_catalog", gated_catalog)
    target = dialogue_entries()[2].identity
    try:
        lazy_editor.open_entry(target)
        assert started.wait(3)
        QApplication.processEvents()
        picker = lazy_editor.view.quest_combo
        lazy_editor.view.translation.setPlainText("draft during catalog read")
        picker.showPopup()
        search(picker, "TestScene")
        assert result_rows(picker) == []
    finally:
        release.set()
        drain(lazy_editor)
    assert picker.search_input.text() == "TestScene"
    assert len(result_rows(picker)) == 1
    assert lazy_editor._selected_key == target
    assert lazy_editor.view.translation.toPlainText() == "draft during catalog read"
    QTest.keyClick(picker.search_input, Qt.Key.Key_Return)
    assert lazy_editor._node_identity[-1] == "SCEN:00000012"


def test_paged_full_directory_delivery_preserves_search_and_commits_a_valid_topic(qapp, tmp_path, monkeypatch):
    started, release = Event(), Event()
    iterate = ProjectStore.iter_entries
    with paged_tests.reopened_dialogue(tmp_path, qapp) as (controller, preview):
        paged_tests.paged._wait_until(qapp, lambda: preview._paging.resident_ready)
        QTest.keyClicks(preview._filters_view.search_all, "First reply")
        paged_tests.paged._wait_until(qapp, lambda: preview._paging._result_key == preview._paging._query_key())

        def gated_scan(*args, **kwargs):
            started.set()
            assert release.wait(10)
            yield from iterate(*args, **kwargs)

        monkeypatch.setattr(ProjectStore, "iter_entries", gated_scan)
        try:
            target = paged_tests.double_click(preview, "First reply")
            paged_tests.paged._wait_until(qapp, started.is_set)
            picker = controller.view.quest_combo
            controller.view.translation.setPlainText("paged draft")
            picker.showPopup()
            search(picker, "00000010")
            assert result_rows(picker)
        finally:
            release.set()
            drain(controller)
        assert picker.search_input.text() == "00000010"
        assert controller._selected_key == target
        assert controller.view.translation.toPlainText() == "paged draft"
        QTest.keyClick(picker.search_input, Qt.Key.Key_Return)
        assert controller._node_identity[-1] == "00000010"
        assert all(controller.context.collection.get(key) is not None for key in controller._navigation_keys)
        controller.discard()
