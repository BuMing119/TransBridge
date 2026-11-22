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
    buttons = preview._summary_view._buttons
    buttons["review"].click()
    assert [entry.id for entry in preview.filtered_entries()] == ["1"]
    assert preview._filters_view.state().search_all == "Dragon"
    assert [key for key, button in buttons.items() if button.isChecked()] == ["review"]

    preview.apply_filter_state(state.to_mapping())
    composition = object.__new__(ShellIntentComposition)
    composition._host = SimpleNamespace(workbench=SimpleNamespace(preview=preview))
    composition._review({})
    assert [entry.id for entry in preview.filtered_entries()] == ["1"]
    assert preview.get_filter_state()["category"] == ["人名"]
    assert preview.get_filter_state()["search_all"] == "Dragon"
    assert buttons["review"].isChecked()

    preview._filters_view.toggle_stage(0)
    assert all(not button.isChecked() for button in buttons.values())
    preview._filters_view.reset_filters()
    assert buttons["total"].isChecked()


def test_scope_does_not_claim_unselected_rows_and_export_remains_whole_file(preview):
    actions = preview._workflow_actions
    assert actions._selection_label.text() == "已选 0 条 / 筛选结果 3 条"
    preview._table.selectRow(0)
    assert actions._selection_label.text() == "已选 1 条 / 筛选结果 3 条"
    preview.apply_filter_state({"search_all": "not present"})
    assert actions._selection_label.text() == "已选 0 条 / 筛选结果 0 条"
    export = actions._buttons[IntentId.PUBLISH_WRITE]
    assert export.isEnabled()
    assert export.text() == "导出文件"
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
    assert not filters.clear_button.isHidden()
    assert "有疑问" in filters.active_summary.text()
    assert "Dragon" in filters.active_summary.text()
    assert [entry.id for entry in preview.filtered_entries()] == ["1"]
    filters.clear_button.click()
    assert filters.state() == FilterState()
    assert len(preview.filtered_entries()) == 3
    assert filters.active_summary.isHidden()
    assert not filters.clear_button.isEnabled()
    assert filters.clear_button.isHidden()


def test_reset_also_clears_labels_and_focus(preview):
    filters = preview._filters_view
    filters.apply_state(FilterState(labels=frozenset({"tag"}), focus_labeled=True))
    filters.reset_filters()
    assert filters.state() == FilterState()


def test_assistant_and_workbench_apply_the_same_filters_in_both_directions(preview):
    from transbridge.smart_assistant.tools.tool_default import _tool_get_current_filters
    from transbridge.smart_assistant.tools.tool_editor import _tool_get_visible_entries, _tool_set_filters

    ctx = preview._ctx
    ctx.label_library = {"review": {"name": "待校对"}}
    assert _tool_set_filters({"labels": ["待校对"]}, ctx).success
    assert preview.get_filter_state()["label"] == ["review"]
    assert _tool_get_visible_entries({}, ctx).data["total_count"] == len(preview.filtered_entries()) == 0
    assert not _tool_set_filters({"search_field": "context", "clear": True}, ctx).success
    assert ctx.filter_state["label"] == ["review"]

    preview.apply_filter_state(FilterState(search_all="Dragon", stages=frozenset({0})).to_mapping())
    assert _tool_get_current_filters({}, ctx).data["active_filter_count"] == 2
    page = _tool_get_visible_entries({}, ctx)
    assert [row["id"] for row in page.data["entries"]] == [entry.id for entry in preview.filtered_entries()] == ["3"]
    assert _tool_set_filters({"search_query": "Wolf", "search_field": "original"}, ctx).success
    assert preview.get_filter_state()["search_all"] == "Dragon"
    assert _tool_get_visible_entries({}, ctx).data["total_count"] == len(preview.filtered_entries()) == 0


def test_saved_search_can_be_cleared_and_ambiguous_label_is_rejected(preview):
    from transbridge.smart_assistant.tools.tool_editor import _tool_set_filters
    from transbridge.smart_assistant.tools.types import ExecutionContext

    app = preview._ctx
    app.label_library = {"a": {"name": "待校对"}, "b": {"name": "待校对"}}
    execution = ExecutionContext(app_context=app)
    object.__setattr__(execution, "filter_state", {"search_query": "editor-id", "search_field": "id"})
    assert not _tool_set_filters({"labels": ["a"]}, execution).success
    assert preview.get_filter_state() == FilterState().to_mapping()
    assert _tool_set_filters({"clear": True, "labels": ["a"]}, execution).success
    assert not _tool_set_filters({"labels": ["待校对"]}, execution).success
    assert preview.get_filter_state()["label"] == ["a"]
    assert _tool_set_filters({"labels": ["missing-label"]}, execution).success
    assert not preview.filtered_entries()
