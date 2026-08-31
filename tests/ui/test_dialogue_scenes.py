from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
import pytest

from tests.dialogue_catalog_support import dialogue_plugin, dialogue_plugin_bytes
from tests.dialogue_support import dialogue_entries
from tests.ui import test_dialogue_editor as editor_tests
from transbridge.application.io.contracts import FormatId, SourceDescriptor, SourceSnapshot
from transbridge.converter.translation_entry_collection import TranslationEntryCollection

editor = editor_tests.editor
drain = editor_tests.drain


@pytest.fixture
def scene_editor(editor):
    editor.context.active_slot.plugin = dialogue_plugin()
    editor.refresh()
    drain(editor)
    editor.open_entry(dialogue_entries()[2].identity)
    return editor


def select_record(editor, row):
    tree = editor.view.tree
    index = editor.view.tree_model.index(row, 0)
    tree.scrollTo(index)
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=tree.visualRect(index).center())


def test_flat_xt_records_have_identifiers_and_clicking_scene_displays_its_references(scene_editor):
    view = scene_editor.view
    model = view.tree_model
    assert view.quest_combo.currentText() == "QUST {Quest} [00000001]"
    assert model.rowCount() == 5
    assert model.rowCount(model.index(0, 0)) == 0
    assert not model.parent(model.index(3, 0)).isValid()
    assert model.data(model.index(1, 0)) == "DIAL {Topic10} [00000010]"
    assert model.data(model.index(2, 0)) == "DIAL {Scene} [00000011]"
    assert model.data(model.index(3, 0)) == "SCEN {TestScene} [00000012]"
    assert "关联词条：5" in model.data(model.index(3, 0), Qt.ItemDataRole.ToolTipRole)
    select_record(scene_editor, 3)
    assert view.table_model.entries == dialogue_entries()[1:]
    assert scene_editor._node_identity[-1] == "SCEN:00000012"


def test_empty_scene_clears_old_editor_keeps_other_records_and_preserves_draft(scene_editor):
    target = dialogue_entries()[2]
    view = scene_editor.view
    view.translation.setPlainText("尚未应用的草稿")
    select_record(scene_editor, 4)
    assert scene_editor._current is None
    assert view.table_model.rowCount() == 0
    assert view.original.toPlainText() == view.translation.toPlainText() == ""
    assert not view.translation.isEnabled()
    assert view.context_panel.isEnabled() and view.tree.isEnabled()
    scene_editor.refresh()
    drain(scene_editor)
    assert scene_editor._node_identity[-1] == "SCEN:00000013"
    assert view.tree.currentIndex().row() == 4
    assert not view.translation.isEnabled()
    scene_editor.open_entry(target.identity)
    assert view.translation.toPlainText() == "尚未应用的草稿"


@pytest.mark.parametrize("advance", [False, True])
def test_applying_scene_entry_retains_scene_selection_and_unique_navigation(scene_editor, advance):
    select_record(scene_editor, 3)
    keys = tuple(entry.identity for entry in dialogue_entries()[1:])
    assert scene_editor._navigation_keys == keys
    scene_editor.view.translation.setPlainText("场景译文")
    scene_editor.apply(advance)
    drain(scene_editor)
    assert scene_editor.context.collection.get(keys[0]).translation == "场景译文"
    assert scene_editor._node_identity[-1] == "SCEN:00000012"
    assert scene_editor.view.tree.currentIndex().row() == 3
    assert scene_editor._current.before.entry_key == keys[int(advance)]
    assert scene_editor._navigation_keys == keys
    scene_editor.move(1)
    assert scene_editor._current.before.entry_key == keys[int(advance) + 1]
    assert scene_editor._node_identity[-1] == "SCEN:00000012"
    # A new main-table double-click returns to the entry's canonical DIAL.
    scene_editor.open_entry(keys[-1])
    assert scene_editor._node_identity[-1] == "00000011"
    assert len(scene_editor._navigation_keys) == len(dialogue_entries())


def test_scene_survives_project_snapshot_hydration_without_a_live_plugin(editor):
    slot = editor.context.active_slot
    slot.source_snapshot = SourceSnapshot.from_bytes(
        SourceDescriptor("absent/fixture.esp"), FormatId.PLUGIN_SSE, dialogue_plugin_bytes()
    )
    slot.plugin = None
    slot.format_id = FormatId.PLUGIN_SSE
    editor.refresh()
    drain(editor)
    editor.open_entry(dialogue_entries()[2].identity)
    select_record(editor, 3)
    assert editor._node_identity[-1] == "SCEN:00000012"
    assert editor.view.table_model.rowCount() == 5


def test_eet_never_reads_scene_catalog_even_if_a_stale_plugin_is_present(scene_editor, monkeypatch):
    calls = []
    monkeypatch.setattr(scene_editor._loader, "build", lambda *args, **kwargs: calls.append(True))
    scene_editor.context.active_slot.format_id = FormatId.XML_EET
    scene_editor.refresh()
    drain(scene_editor)
    assert calls == []
    assert not scene_editor.view.context_panel.isEnabled()
    assert scene_editor.view.tree_model.rowCount() == 0
    assert scene_editor.view.translation.isEnabled()
    assert "EET" in scene_editor.view.context_reason.text()


def test_empty_collection_after_empty_scene_clears_stale_record_selection(scene_editor):
    select_record(scene_editor, 4)
    scene_editor.context.collection = TranslationEntryCollection()
    drain(scene_editor)
    assert scene_editor._node_identity is None
    assert not scene_editor.view.body.isEnabled()
    assert "没有可编辑" in scene_editor.view.message.text()


def test_window_cleanup_can_be_called_again_without_disconnect_errors(scene_editor):
    scene_editor.close()
    scene_editor.close()
    assert not scene_editor.dialog.isVisible()
