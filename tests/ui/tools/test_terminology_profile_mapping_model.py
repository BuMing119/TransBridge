from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QComboBox, QLineEdit, QMessageBox, QTableView
import pytest

from transbridge.application.terminology_profiles import (
    InMemoryTerminologyProfileRepository,
    ProfileTermMapping,
    TerminologyProfileContent,
    TerminologyProfileService,
)
from transbridge.ui.tools.terminology_profiles import TerminologyProfileManagerDialog, manager_dialog as manager_module
from transbridge.ui.tools.terminology_profiles.mapping_editor import ProfileMappingEditor
from transbridge.ui.tools.terminology_profiles.mapping_model import ProfileMappingModel

_APP = QApplication.instance() or QApplication([])


def test_raw_input_can_be_invalid_then_reverted_without_losing_dirty_protection():
    model = ProfileMappingModel()
    mapping = ProfileTermMapping("Dragon", "龙", "默认龙")
    model.load((mapping,))
    observed = []
    model.dirty_changed.connect(observed.append)
    target = model.index(0, 2)
    assert model.setData(target, "")
    assert model.is_dirty
    assert target.data() == ""
    with pytest.raises(ValueError, match="profile translation"):
        model.read_mappings()
    assert model.setData(target, "  ")
    assert target.data(Qt.ItemDataRole.EditRole) == "  "
    assert model.setData(target, "龙")
    assert not model.is_dirty
    assert observed == [True, False]
    assert model.read_mappings() == (mapping,)


def test_scope_updates_plugin_editability_and_clears_stale_plugin():
    model = ProfileMappingModel()
    mapping = ProfileTermMapping("Dragon", "龙")
    model.load((mapping,))
    scope, plugin = model.index(0, 3), model.index(0, 4)
    assert scope.data() == "整个工程"
    assert not model.flags(plugin) & Qt.ItemFlag.ItemIsEditable
    assert not model.setData(plugin, "Skyrim.esm")
    assert not model.setData(scope, "invalid")
    assert model.setData(scope, "plugin")
    assert scope.data() == "仅指定插件"
    assert model.flags(plugin) & Qt.ItemFlag.ItemIsEditable
    with pytest.raises(ValueError, match="plugin ID"):
        model.read_mappings()
    assert model.setData(plugin, "  Skyrim.esm  ")
    assert model.read_mappings()[0].plugin_id == "Skyrim.esm"
    assert model.setData(scope, "project")
    assert plugin.data() == ""
    assert not model.flags(plugin) & Qt.ItemFlag.ItemIsEditable
    assert not model.is_dirty


def test_structural_edits_and_reverts_track_exact_ordered_content():
    first, second = ProfileTermMapping("A", "甲"), ProfileTermMapping("B", "乙")
    model = ProfileMappingModel()
    model.load((first, second))
    model.append_mapping()
    assert model.is_dirty
    assert model.removeRow(2)
    assert not model.is_dirty
    assert model.removeRow(1)
    assert model.is_dirty
    model.append_mapping(second)
    assert not model.is_dirty
    assert model.removeRow(0)
    model.append_mapping(first)
    assert model.is_dirty
    assert model.read_mappings() == (second, first)
    model.load((first, second))
    assert not model.is_dirty
    assert not model.removeRow(-1)


def test_large_editor_keeps_rows_virtual_and_creates_only_the_active_scope_editor():
    editor = ProfileMappingEditor()
    mappings = tuple(ProfileTermMapping(f"Term {index}", f"译名 {index}") for index in range(2000))
    editor.load(mappings)
    editor.resize(900, 500)
    editor.show()
    _APP.processEvents()
    assert isinstance(editor, QTableView)
    assert editor.model().rowCount() == 2000
    assert not editor.findChildren(QComboBox)
    assert not editor.findChildren(QLineEdit)
    index = editor.model().index(1999, 3)
    editor.setCurrentIndex(index)
    editor.scrollTo(index)
    editor.edit(index)
    _APP.processEvents()
    editors = editor.findChildren(QComboBox)
    assert len(editors) == 1
    combo = editors[0]
    QTest.keyClick(combo, Qt.Key.Key_Down)
    QTest.keyClick(combo, Qt.Key.Key_Enter)
    _APP.processEvents()
    assert index.data(Qt.ItemDataRole.EditRole) == "plugin"
    assert editor.model().flags(editor.model().index(1999, 4)) & Qt.ItemFlag.ItemIsEditable
    assert editor.is_dirty
    _APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not editor.findChildren(QComboBox)
    editor.close()


@pytest.mark.parametrize("operation", ["save", "close", "switch"])
def test_manager_protects_active_delegate_text_before_focus_leaves(monkeypatch, operation):
    service = TerminologyProfileService(InMemoryTerminologyProfileRepository())
    first = service.create_with_content(
        "project", "A", TerminologyProfileContent(mappings=(ProfileTermMapping("Dragon", "saved"),))
    )
    second = service.create("project", "B")
    dialog = TerminologyProfileManagerDialog(service, "project", selected_profile_id=first.profile_id)
    dialog.show()
    index = dialog.mapping_table.model().index(0, 2)
    dialog.mapping_table.setCurrentIndex(index)
    dialog.mapping_table.edit(index)
    _APP.processEvents()
    line = dialog.mapping_table.findChild(QLineEdit)
    assert line is not None
    line.setText("typed but not yet committed")
    assert index.data() == "saved"
    choices = []
    monkeypatch.setattr(
        manager_module,
        "choose_unsaved_action",
        lambda *_args: choices.append(True) or QMessageBox.StandardButton.Cancel,
    )
    if operation == "save":
        assert dialog._save_draft()
        assert service.list_profiles("project")[0].draft.mappings[0].translation == "typed but not yet committed"
    else:
        if operation == "close":
            dialog.close()
        else:
            assert not dialog.select_profile(second.profile_id)
        assert choices == [True]
        assert dialog.isVisible()
        assert dialog.current_profile.profile_id == first.profile_id
        assert dialog.mapping_table.model().index(0, 2).data() == "typed but not yet committed"
    monkeypatch.setattr(manager_module, "choose_unsaved_action", lambda *_args: QMessageBox.StandardButton.Discard)
    dialog.close()
    _APP.processEvents()
