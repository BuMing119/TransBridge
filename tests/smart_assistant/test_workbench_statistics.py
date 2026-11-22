"""Check the public statistics tool against the workbench summary and filter buttons."""

from types import SimpleNamespace

import pytest

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.smart_assistant.context_builder import ContextBuilder
from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.smart_assistant.tools import register_all
from transbridge.smart_assistant.tools.types import ExecutionContext
from transbridge.ui.context import CollectionSlot
from transbridge.ui.workbench.step2 import Step2PreviewWidget


def test_statistics_matches_visible_workbench_groups(current_project_factory):
    entries = TranslationEntryCollection([
        TranslationEntry("a", "a", "Untranslated", "草稿", 0, "BOOK:FULL"),
        TranslationEntry("b", "b", "Question", "疑问", 2, "BOOK:FULL"),
        TranslationEntry("c", "c", "Reviewed", "", 5, "BOOK:FULL"),
        TranslationEntry("d", "d", "Hidden", "隐藏", -1, "BOOK:FULL"),
    ])
    project = current_project_factory(entries)
    widget = Step2PreviewWidget(project.context)
    register_all()
    try:
        widget.refresh(project.context.collection)
        result = ToolRegistry.get("get_statistics").execute({}, ExecutionContext(project.context))
        assert result.success, result.message
        data = result.data
        assert data["translated"] == widget._summary.completed == 1
        assert data["untranslated"] == widget._summary.untranslated == 1
        assert data["needs_review"] == widget._summary.needs_review == 1
        assert data["hidden"] == widget._summary.hidden == 1
        assert data["total"] == widget._summary.total == 4
        assert data["translation_rate"] == 25.0
        assert data["category_distribution"] == {"书名": 4}
        snapshot = ContextBuilder(project.context).build()
        assert "4 total, 1 translated, 1 untranslated, 1 in question, 1 hidden" in snapshot
        assert "书名: 4 entries" in snapshot
        categories = widget._filters_view.category_container
        assert "书名 4" in [categories.itemAt(i).widget().text() for i in range(categories.count())]
    finally:
        widget.close()
        widget.deleteLater()


def test_empty_statistics_exposes_all_workbench_groups():
    register_all()
    result = ToolRegistry.get("get_statistics").execute({}, SimpleNamespace(collection=None))
    assert result.success
    assert result.data["total"] == result.data["needs_review"] == result.data["hidden"] == 0
    assert result.data["stage_distribution"] == result.data["category_distribution"] == {}


@pytest.mark.parametrize("name", ["get_statistics", "list_labels"])
def test_switch_during_statistics_read_rejects_the_old_result(current_project_factory, monkeypatch, name):
    entries = TranslationEntryCollection([TranslationEntry("a", "a", "First", "", 0, "BOOK:FULL")])
    app = current_project_factory(entries).context
    context = ExecutionContext(app)
    read = app.workbench_statistics

    def switch_after_read(collection, **kwargs):
        value = read(collection, **kwargs)
        app.add_slot("other", CollectionSlot("Other source", TranslationEntryCollection()))
        return value

    monkeypatch.setattr(app, "workbench_statistics", switch_after_read)
    register_all()
    result = ToolRegistry.get(name).execute({}, context)
    assert not result.success
    assert "来源" in result.message
