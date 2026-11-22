"""Compare public assistant tools with actual workbench editing gestures on identical projects."""

from PyQt6.QtCore import Qt
import pytest

from tests.conftest import make_test_collection
from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.smart_assistant.tools import register_all
from transbridge.smart_assistant.tools.types import ExecutionContext
from transbridge.ui.workbench.step2 import Step2PreviewWidget
from transbridge.ui.workbench.translation_table import COL_TRANSLATION


def _state(project):
    return {
        e.entry_key: (e.translation, e.stage.value, e.labels, e.external_refs)
        for e in project.aggregate.snapshot().entries
    }


@pytest.fixture
def editors(current_project_factory):
    desktop = current_project_factory(make_test_collection(3))
    assistant = current_project_factory(make_test_collection(3))
    widget = Step2PreviewWidget(desktop.context)
    widget.refresh(desktop.context.collection)
    register_all()
    try:
        yield desktop, assistant, widget
    finally:
        widget.close()
        widget.deleteLater()


@pytest.mark.parametrize(
    ("row", "text", "expected", "stage"),
    [(0, "  首次译文\n", "首次译文", 1), (1, "  （无译文）  ", "", 1), (2, "  已修正  ", "已修正", 2)],
)
def test_inline_edit_and_tool_store_identical_translation_and_stage(editors, row, text, expected, stage):
    desktop, assistant, widget = editors
    table = widget._table
    entry = table.entry_at_row(row)
    assert table.model().setData(table.model().index(row, COL_TRANSLATION), text, Qt.ItemDataRole.EditRole)
    result = ToolRegistry.get("edit_translation").execute(
        {"entry_id": entry.key, "new_translation": text}, ExecutionContext(assistant.context)
    )
    assert result.success, result.message
    assert result.data["stage"] == stage
    assert result.data["new_translation"] == expected
    assert _state(desktop) == _state(assistant)
    assert _state(desktop)[entry.identity][:2] == (expected, stage)


def test_stage_menu_and_tool_change_only_the_selected_rows(editors):
    desktop, assistant, widget = editors
    table = widget._table
    table.select_rows((0, 2))
    entries = [table.entry_at_row(row) for row in range(3)]
    before = _state(desktop)
    menu = widget._build_context_menu(0)
    stages = next(action.menu() for action in menu.actions() if action.text() == "翻译状态")
    next(action for action in stages.actions() if action.text() == "已检查").trigger()
    result = ToolRegistry.get("set_stage").execute(
        {"entry_ids": [entries[0].key, entries[2].key], "stage": 3}, ExecutionContext(assistant.context)
    )
    assert result.success, result.message
    after = _state(assistant)
    assert _state(desktop) == after
    assert [after[entry.identity][1] for entry in entries] == [3, 1, 3]
    assert all(after[key][0] == before[key][0] for key in before)


def test_explicit_stage_overrides_promotion_in_a_single_tool_edit(editors):
    desktop, assistant, widget = editors
    table = widget._table
    entry = table.entry_at_row(0)
    assert table.model().setData(table.model().index(0, COL_TRANSLATION), "  保存  ", Qt.ItemDataRole.EditRole)
    table.select_rows((0,))
    menu = widget._build_context_menu(0)
    stages = next(action.menu() for action in menu.actions() if action.text() == "翻译状态")
    next(action for action in stages.actions() if action.text() == "已检查").trigger()

    revision = assistant.aggregate.snapshot().revision
    result = ToolRegistry.get("edit_translation").execute(
        {"entry_id": entry.key, "new_translation": "  保存  ", "new_stage": 3}, ExecutionContext(assistant.context)
    )
    assert result.success, result.message
    assert assistant.aggregate.snapshot().revision == revision + 1
    assert _state(assistant)[entry.identity][:2] == ("保存", 3)
    assert _state(assistant) == _state(desktop)
