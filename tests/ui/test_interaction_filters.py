from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.converter.translation_entry import STAGE_QUESTIONABLE, TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui import context as context_module
from transbridge.ui.shell.action_catalog import IntentId
from transbridge.ui.shell.intent_composition import ShellIntentComposition
from transbridge.ui.workbench.filters_presenter import FilterState
from transbridge.ui.workbench.step2 import Step2PreviewWidget

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def preview(monkeypatch):
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token=""))
    context = context_module.AppContext()
    widget = Step2PreviewWidget(context)
    entries = [
        TranslationEntry("1", "key-1", "Dragon", "巨龙", STAGE_QUESTIONABLE, "NPC_:FULL"),
        TranslationEntry("2", "key-2", "Wolf", "狼", STAGE_QUESTIONABLE, "NPC_:FULL"),
        TranslationEntry("3", "key-3", "Dragon", "", 0, "NPC_:FULL"),
    ]
    context.add_slot(
        "demo",
        context_module.CollectionSlot(
            label="Demo", collection=TranslationEntryCollection(entries), esp_path="Demo.esp"
        ),
    )
    yield widget
    widget.close()


def test_summary_and_review_retain_text_and_other_filters(preview):
    state = FilterState(search_all="Dragon", categories=frozenset({"人名"}), search_key="key")
    preview.apply_filter_state(state.to_mapping())
    preview._summary_view._buttons["review"].click()
    assert [entry.id for entry in preview.filtered_entries()] == ["1"]
    assert preview._filters_view.state().search_all == "Dragon"

    preview.apply_filter_state(state.to_mapping())
    composition = object.__new__(ShellIntentComposition)
    composition._host = SimpleNamespace(workbench=SimpleNamespace(preview=preview))
    composition._review({})
    assert [entry.id for entry in preview.filtered_entries()] == ["1"]
    assert preview.get_filter_state()["category"] == ["人名"]
    assert preview.get_filter_state()["search_all"] == "Dragon"


def test_scope_does_not_claim_unselected_rows_and_export_remains_whole_file(preview):
    actions = preview._workflow_actions
    assert actions._selection_label.text() == "已选 0 条 / 筛选结果 3 条"
    preview._table.selectRow(0)
    assert actions._selection_label.text() == "已选 1 条 / 筛选结果 3 条"
    preview.apply_filter_state({"search_all": "not present"})
    assert actions._selection_label.text() == "已选 0 条 / 筛选结果 0 条"
    export = actions._buttons[IntentId.PUBLISH_WRITE]
    assert export.isEnabled()
    assert export.text() == "导出整个文件"
    assert "不受表格选择或筛选限制" in export.toolTip()
    assert len(preview._ctx.collection) == 3


def test_hidden_filters_are_visible_in_summary_and_reset_clears_every_dimension(preview):
    filters = preview._filters_view
    preview.apply_filter_state(
        FilterState(
            categories=frozenset({"人名"}),
            stages=frozenset({STAGE_QUESTIONABLE}),
            search_all="Dragon",
            search_key="key",
            search_original="Dragon",
            search_translation="巨龙",
        ).to_mapping()
    )
    filters.filter_button.setChecked(False)
    filters.advanced_button.setChecked(False)
    assert filters.stage_widget.isHidden()
    assert not filters.active_summary.isHidden()
    assert "有疑问" in filters.active_summary.text()
    assert "Dragon" in filters.active_summary.text()
    assert [entry.id for entry in preview.filtered_entries()] == ["1"]
    filters.clear_button.click()
    assert filters.state() == FilterState()
    assert len(preview.filtered_entries()) == 3
    assert filters.active_summary.isHidden()
    assert not filters.clear_button.isEnabled()


def test_reset_also_clears_labels_and_focus(preview):
    filters = preview._filters_view
    filters.apply_state(FilterState(labels=frozenset({"tag"}), focus_labeled=True))
    filters.reset_filters()
    assert filters.state() == FilterState()
