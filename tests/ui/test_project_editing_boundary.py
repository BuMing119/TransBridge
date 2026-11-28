"""Display-only collections cannot acquire a second project editing runtime."""

from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication, QMessageBox
import pytest

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.context import AppContext, CollectionSlot
from transbridge.ui.dialogue.editing import EntryDraft
from transbridge.ui.workbench.step2 import Step2PreviewWidget
from transbridge.ui.workbench.translation_reset import TranslationResetAction
from transbridge.ui.workbench.translation_stage_action import TranslationStageAction


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def test_standalone_display_rejects_all_project_edits_and_retains_drafts(qapp, monkeypatch):
    monkeypatch.setattr("transbridge.ui.context.ParatranzConfig.create_or_load", lambda: SimpleNamespace(token=""))
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: warnings.append(_args[-1]))
    entry = TranslationEntry("one", "one", "Original", "Saved", 1, "NPC_:FULL")
    context = AppContext()
    context.add_slot("fixture", CollectionSlot("Fixture", TranslationEntryCollection((entry,))))
    view = Step2PreviewWidget(context)
    view.refresh(context.collection)
    try:
        draft = EntryDraft.capture(context, entry)
        draft.text = "Draft"
        assert "写入服务不可用" in draft.commit(context)
        assert draft.text == "Draft"
        assert not TranslationResetAction(context, entry, entries=(entry,), selected_ids=(), parent=view).run()
        assert not TranslationStageAction(context, entry, 5, entries=(entry,), selected_ids=(), parent=view).run()
        assert not view._commit_labels({"one": {"new"}}, {"new": {"name": "New"}})
        view._inline_edits.edit_requested(entry, "Inline draft", 0)
        assert view._inline_edits.view.text.toPlainText() == "Inline draft"
        assert (entry.translation, entry.stage) == ("Saved", 1)
        assert context.entry_labels == {} and not context.dirty
        assert len(warnings) == 3
    finally:
        view.close()
        context.close_projection()
