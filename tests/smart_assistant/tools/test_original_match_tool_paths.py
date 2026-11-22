from types import SimpleNamespace

import pytest

from transbridge.application.assistant_requests.models import RequestError
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.smart_assistant.request_scope import apply_captured_scope
from transbridge.smart_assistant.tools.tool_editor import EditorController
from transbridge.ui.tools.smart_assistant.request_binding import RequestBinding


def _entries():
    return tuple(
        TranslationEntry("same", "same", original, f"old-{original}", 1, "QUST:CNAM", requires_original_match=True)
        for original in ("first", "second")
    )


def test_assistant_query_ids_support_exact_edit_stage_and_selection(current_project_factory):
    from transbridge.ui.workbench.step2 import Step2PreviewWidget

    first, second = _entries()
    collection = TranslationEntryCollection((first, second))
    project = current_project_factory(collection)
    context = project.context
    controller = EditorController()
    rows = controller.get_visible_entries({}, context, collection).data["entries"]
    first_id = rows[0]["key"]
    assert first_id == first.identity.serialize()
    assert rows[1]["key"] == second.identity.serialize()
    assert controller.edit_translation({"entry_id": first_id, "new_translation": "chosen"}, context, collection).success
    assert tuple(context.collection.get(entry.identity).translation for entry in (first, second)) == (
        "chosen",
        "old-second",
    )
    assert controller.set_stage({"entry_ids": [first_id], "stage": 3}, context, context.collection).success
    assert tuple(context.collection.get(entry.identity).stage for entry in (first, second)) == (3, 1)
    preview = Step2PreviewWidget(context)
    try:
        preview.refresh(context.collection)
        assert controller.select_entries({"entry_ids": [first_id]}, context).success
        assert preview.selected_row_entry_ids() == (first_id,)
        assert context.selected_ids == {first_id}
        assert not controller.select_entries({"entry_ids": ["same"]}, context).success
    finally:
        preview.close()
    before = project.aggregate.snapshot()
    result = controller.edit_translation({"entry_id": "same", "new_translation": "wrong"}, context, collection)
    assert not result.success and "完整 EntryKey" in result.message
    assert project.aggregate.snapshot() is before
    assert (first.id, first.key) == ("same", "same")


def test_selection_provider_detaches_when_qt_destroys_the_table(current_project_factory):
    from PyQt6.QtCore import QCoreApplication, QEvent

    from transbridge.ui.workbench.step2 import Step2PreviewWidget

    project = current_project_factory(TranslationEntryCollection(_entries()))
    preview = Step2PreviewWidget(project.context)
    preview.refresh(project.context.collection)
    preview._table.selectRow(0)
    assert project.context.selected_ids
    preview.deleteLater()
    QCoreApplication.sendPostedEvents(preview, QEvent.Type.DeferredDelete)
    assert not project.context.selected_ids
    assert project.context.workbench_row_selection is None


def test_selection_uses_distinct_keys_when_display_ids_repeat(current_project_factory):
    from tests.ui.test_translation_reset import _stage_action
    from transbridge.ui.tools.ai_translator.scope_presenter import ScopePresenter, Step2ScopeAdapter
    from transbridge.ui.tools.ai_translator.task_scope import TaskScope
    from transbridge.ui.workbench.step2 import Step2PreviewWidget
    from transbridge.ui.workbench.translation_table_columns import COL_ORIGINAL

    entries = (
        TranslationEntry("shared", "a", "Zebra", "", 0, ""),
        TranslationEntry("shared", "b", "Ant", "", 0, ""),
    )
    context = current_project_factory(TranslationEntryCollection(entries)).context
    preview = Step2PreviewWidget(context)
    try:
        preview.refresh(context.collection)
        select = EditorController().select_entries
        assert select({"entry_ids": ["a"]}, context).data["selected_ids"] == ["a"]
        preview._table.horizontalHeader().sectionClicked.emit(COL_ORIGINAL)
        assert [entry.key for entry in context.selected_entries] == ["a"]
        workbench = Step2ScopeAdapter(preview)
        scope = ScopePresenter(lambda: context.collection, lambda: context.entry_labels, lambda _: "", workbench)
        state = scope.select_preset("selection")
        assert [entry.key for entry in scope.candidates()] == ["a"]
        tasks = TaskScope(context, workbench, lambda _: "").build(
            [context.active_slot], state, mode="translate", config=SimpleNamespace(), overwrite=False
        )
        assert [entry.key for entry in tasks[0].translate_entries] == ["a"]
        _stage_action(preview, preview._table.row_for_identity(entries[0].identity), "已检查").trigger()
        assert [context.collection.get(entry.identity).stage for entry in entries] == [3, 0]
        with pytest.warns(RuntimeWarning, match="ambiguous"):
            assert not select({"entry_ids": ["shared"]}, context).success
        result = select({"entry_ids": [entries[1].identity.serialize()]}, context)
        assert result.data["selected_count"] == 2 and set(result.data["selected_ids"]) == {"a", "b"}
        assert context.selected_ids == {"a", "b"}
    finally:
        preview.close()


def test_captured_selection_roundtrip_keeps_only_selected_original():
    first, second = _entries()
    collection = TranslationEntryCollection((first, second))
    app = SimpleNamespace(selected_entries=(first,), filter_state={}, translation_scope={})
    selection = RequestBinding._selection(SimpleNamespace(facade=SimpleNamespace(_ctx=app)))
    assert selection["selected_entry_ids"] == [first.identity.serialize()]
    context = SimpleNamespace(_target_collection=collection)
    request = SimpleNamespace(source_message_ids=("message",), execution_version_json="")
    apply_captured_scope(request, [{"message_id": "message", "selection": selection}], context)
    assert context.selected_entries == (first,)
    selection["selected_entry_ids"] = ["same"]
    with pytest.raises(RequestError, match="REQUEST_SCOPE_MISMATCH"):
        apply_captured_scope(request, [{"message_id": "message", "selection": selection}], context)
