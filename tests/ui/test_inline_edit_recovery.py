from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui import context as context_module
from transbridge.ui.workbench.step2 import Step2PreviewWidget
from transbridge.ui.workbench.translation_table_columns import COL_TRANSLATION

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def editing(monkeypatch):
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token=""))
    context = context_module.AppContext()
    entries = [TranslationEntry("1", "key", "Original", "Old", 1, "NPC_:FULL")]
    widget = Step2PreviewWidget(context)
    context.add_slot(
        "demo",
        context_module.CollectionSlot(
            label="Demo", collection=TranslationEntryCollection(entries), esp_path="Demo.esp"
        ),
    )
    monkeypatch.setattr(type(context), "uses_authoritative_projection", property(lambda _: True))
    response = SimpleNamespace(is_success=False, diagnostics=[SimpleNamespace(message="文件暂时不可写")])
    monkeypatch.setattr(context, "update_projected_entry", lambda *_args, **_kwargs: response)
    yield widget, context, entries[0], response
    widget.close()


def test_failed_edit_retains_complete_input_then_retries(editing):
    widget, _context, entry, response = editing
    text = "  " + "完整译文" * 40 + "\n第二行  "
    widget._table.model().setData(widget._table.model().index(0, COL_TRANSLATION), text, Qt.ItemDataRole.EditRole)
    view = widget._inline_edits.view
    assert entry.translation == "Old"
    assert widget._table.model().index(0, COL_TRANSLATION).data(Qt.ItemDataRole.DisplayRole) == "Old"
    assert not view.isHidden()
    assert "文件暂时不可写" in view.message.text()
    assert view.text.toPlainText() == text
    view.copy_button.click()
    assert QApplication.clipboard().text() == text
    response.is_success = True
    view.retry_button.click()
    assert entry.translation == text.strip()
    assert view.isHidden()


def test_changed_entry_blocks_retry_without_losing_draft(editing):
    widget, _context, entry, response = editing
    widget._table.model().setData(widget._table.model().index(0, COL_TRANSLATION), "Draft", Qt.ItemDataRole.EditRole)
    entry.translation = "New committed value"
    response.is_success = True
    widget._inline_edits.retry()
    assert entry.translation == "New committed value"
    view = widget._inline_edits.view
    assert "词条已变化" in view.message.text()
    assert not view.retry_button.isEnabled()
    assert view.text.toPlainText() == "Draft"


def test_collection_switch_blocks_old_draft_and_explicit_discard_clears_it(editing):
    widget, context, entry, response = editing
    widget._table.model().setData(widget._table.model().index(0, COL_TRANSLATION), "Draft", Qt.ItemDataRole.EditRole)
    other = TranslationEntry("1", "key", "Other", "Other translation", 1, "NPC_:FULL")
    context.add_slot(
        "other",
        context_module.CollectionSlot(
            label="Other", collection=TranslationEntryCollection([other]), esp_path="Other.esp"
        ),
    )
    response.is_success = True
    widget._inline_edits.retry()
    assert entry.translation == "Old"
    assert other.translation == "Other translation"
    view = widget._inline_edits.view
    assert "已切换" in view.message.text()
    assert view.text.toPlainText() == "Draft"
    view.discard_button.click()
    assert view.isHidden()


def test_commit_exception_keeps_input_and_diagnostic(editing, monkeypatch, caplog):
    widget, context, entry, _response = editing

    def fail(*_args, **_kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(context, "update_projected_entry", fail)
    widget._table.model().setData(widget._table.model().index(0, COL_TRANSLATION), "Draft", Qt.ItemDataRole.EditRole)
    assert entry.translation == "Old"
    assert widget._inline_edits.view.text.toPlainText() == "Draft"
    assert "disk unavailable" in widget._inline_edits.view.message.text()
    assert "Inline translation commit failed" in caplog.text


def test_multiple_original_qualified_entries_keep_independent_drafts(editing):
    widget, context, _entry, response = editing
    entries = [
        TranslationEntry(
            "1",
            "key",
            original,
            "Old",
            1,
            "NPC_:FULL",
            entry_key=EntryKey(SourceNamespace.legacy(), "key", original),
        )
        for original in ("First", "Second")
    ]
    context.collection = TranslationEntryCollection(entries)
    widget._table.model().setData(
        widget._table.model().index(0, COL_TRANSLATION), "Draft first", Qt.ItemDataRole.EditRole
    )
    widget._table.model().setData(
        widget._table.model().index(1, COL_TRANSLATION), "Draft second", Qt.ItemDataRole.EditRole
    )
    view = widget._inline_edits.view
    assert view.drafts.count() == 2
    view.drafts.setCurrentIndex(0)
    assert view.text.toPlainText() == "Draft first"
    response.is_success = True
    view.retry_button.click()
    assert entries[0].translation == "Draft first"
    assert entries[1].translation == "Old"
    assert view.drafts.count() == 1
    assert view.text.toPlainText() == "Draft second"


@pytest.mark.parametrize("target", [("other-project", "v1"), ("project", "v2")])
def test_project_and_variant_switch_require_returning_to_original_scope(editing, monkeypatch, target):
    widget, context, entry, response = editing
    active = [("project", "v1")]
    monkeypatch.setattr(type(context), "active_version_identity", property(lambda _: active[0]))
    widget._table.model().setData(widget._table.model().index(0, COL_TRANSLATION), "Draft", Qt.ItemDataRole.EditRole)
    active[0] = target
    context.project_changed.emit()
    response.is_success = True
    widget._inline_edits.retry()
    assert entry.translation == "Old"
    assert not widget._inline_edits.view.retry_button.isEnabled()
    active[0] = ("project", "v1")
    context.project_changed.emit()
    assert widget._inline_edits.view.retry_button.isEnabled()
    widget._inline_edits.view.retry_button.click()
    assert entry.translation == "Draft"
