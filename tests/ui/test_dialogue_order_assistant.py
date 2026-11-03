"""Assistant queries and writes keep stable identity after cached display ordering."""

from types import SimpleNamespace

from tests.ui.test_workbench_dialogue_reading_order import (
    _APP,
    _READING_TEXTS,
    texts,
    wait_for,
)
from transbridge.application.translation.entry_alias import ai_entry_key
from transbridge.smart_assistant.tools.tool_editor import EditorController
from transbridge.ui.project_labels import entry_label_key
from transbridge.ui.shell.assistant_navigation import locate_assistant_entries

pytest_plugins = ["tests.ui.test_workbench_dialogue_reading_order"]


def test_query_edit_and_navigation_keep_identity_after_reading_sort(make_view):
    view, project, source_entries = make_view(prepared=True)
    context = project.context
    wait_for(lambda: texts(view) == _READING_TEXTS)
    source_keys = tuple(entry.identity for entry in source_entries)
    displayed_keys = tuple(view._table.entry_at_row(row).identity for row in range(view._table.rowCount()))
    assert displayed_keys != source_keys
    controller = EditorController()
    query = controller.get_visible_entries({"limit": 200}, context, context.collection)
    assert query.success
    assert [row["key"] for row in query.data["entries"]] == [ai_entry_key(entry) for entry in source_entries]
    source_row = next(index for index, key in enumerate(source_keys) if displayed_keys.index(key) != index)
    wanted = source_entries[source_row]
    target_key = query.data["entries"][source_row]["key"]
    displayed_row = displayed_keys.index(wanted.identity)
    page = controller.get_visible_entries({"offset": source_row, "limit": 1}, context, context.collection)
    assert page.data["entries"][0]["key"] == target_key
    result = controller.edit_translation(
        {"entry_id": target_key, "new_translation": "Updated translation"}, context, context.collection
    )
    assert result.success, result
    _APP.processEvents()
    assert tuple(entry.identity for entry in context.collection) == source_keys
    for original in source_entries:
        current = context.collection.get(original.identity)
        assert (current.original, current.context, current.metadata, current.stage) == (
            original.original,
            original.context,
            original.metadata,
            original.stage,
        )
        assert current.translation == (
            "Updated translation" if original.identity == wanted.identity else original.translation
        )
    assert texts(view) == _READING_TEXTS
    assert view._table.entry_at_row(displayed_row).translation == "Updated translation"
    navigated = []
    host = SimpleNamespace(
        context=context,
        workbench=view,
        show_workbench=lambda: navigated.append("show"),
        raise_=lambda: navigated.append("raise"),
        activateWindow=lambda: navigated.append("activate"),
    )
    project_id, variant_id = context.active_version_identity
    view._table.selectRow(source_row)
    locate_assistant_entries(host, SimpleNamespace(project_id=project_id, variant_id=variant_id), [target_key])
    _APP.processEvents()
    assert navigated == ["show", "raise", "activate"]
    assert view._table.currentIndex().row() == displayed_row
    assert view._table.entry_at_row(displayed_row).identity == wanted.identity
    assert view._table.selected_entry_ids() == (entry_label_key(wanted),)
