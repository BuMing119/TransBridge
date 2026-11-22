from dataclasses import replace

from tests.conftest import make_test_collection
from tests.plugin_fixtures import write_plugin
from tests.smart_assistant.tools.test_source_import_authority import project as stored_project
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.persistence.project_store.paged_reads import WORKBENCH_PAGE_SIZE
from transbridge.smart_assistant.tools.tool_default import _tool_get_statistics
from transbridge.smart_assistant.tools.tool_editor import _tool_list_labels, _tool_manage_entry_labels
from transbridge.smart_assistant.tools.tool_parser import _tool_parse_esp
from transbridge.smart_assistant.tools.types import ExecutionContext
from transbridge.ui.project_rows import build_paged_slots
from transbridge.ui.workbench.step2 import Step2PreviewWidget

__all__ = ["stored_project"]


def test_duplicate_display_id_refuses_both_menu_and_filtered_label_edits(current_project_factory):
    entries = tuple(replace(entry, id="shared display id") for entry in make_test_collection(2))
    project = current_project_factory(entries, label_library={"review": {"name": "Review", "color": "#123456"}})
    app = project.context
    widget = Step2PreviewWidget(app)
    try:
        widget.refresh(app.collection)
        menu = widget._build_context_menu(0)
        labels = next(action.menu() for action in menu.actions() if action.text() == "标签")
        actions = labels.actions()
        assert any("重复" in action.text() for action in actions)
        assert not next(action for action in actions if "● Review" == action.text()).isEnabled()
        context = ExecutionContext(app_context=app)
        app.set_filter(search_key=entries[0].key)
        baseline = project.aggregate.snapshot()
        result = _tool_manage_entry_labels({"action": "batch_assign", "name": "Review"}, context)
        assert not result.success and "重复" in result.message
        assert project.aggregate.snapshot() is baseline
    finally:
        widget.close()


def test_empty_and_already_assigned_filter_issue_no_label_write(current_project_factory, monkeypatch):
    project = current_project_factory(make_test_collection(3), label_library={"review": {"name": "待校对"}})
    app = project.context
    context = ExecutionContext(app_context=app)
    args = {"action": "batch_assign", "name": "待校对"}
    app.set_filter(label=["review"])
    revision = app.variant_revision
    calls = []
    replace = app.replace_projected_labels

    def track(*args, **kwargs):
        calls.append(args)
        return replace(*args, **kwargs)

    monkeypatch.setattr(app, "replace_projected_labels", track)
    result = _tool_manage_entry_labels(args, context)
    assert result.success and result.data == {"assigned_count": 0, "filter_total": 0}
    assert not calls and app.variant_revision == revision

    app.clear_filters()
    result = _tool_manage_entry_labels(args, context)
    assert result.success and result.data == {"assigned_count": 3, "filter_total": 3}
    assert len(calls) == 1
    revision = app.variant_revision
    result = _tool_manage_entry_labels(args, context)
    assert result.success and result.data == {"assigned_count": 0, "filter_total": 3}
    assert len(calls) == 1 and app.variant_revision == revision


def test_scoped_offscreen_batch_keeps_existing_labels_and_captured_membership(stored_project, monkeypatch):
    project = stored_project
    app = project.ctx
    path = write_plugin(
        project.root / "large.esp",
        [(0x800 + index, f"Name{index:04}", f"Original {index:04}") for index in range(WORKBENCH_PAGE_SIZE + 4)],
    )
    read = _tool_parse_esp({"path": str(path)}, ExecutionContext(app_context=app))
    assert read.success, read.message
    entries = tuple(app.collection)
    last, previous = entries[-1], entries[-2]
    labels = {"old": {"name": "已有"}, "new": {"name": "待校对"}}
    assert app.replace_projected_labels({last.id: {"old"}, previous.id: {"old"}}, labels).is_success
    assert project.services.gui_project_commands.save(project.request).is_success
    opener = project.services.current_project_opener
    prepared = opener.prepare_active(project.request, paged=True)
    assert prepared.is_success, prepared.diagnostics
    token = app.begin_project_open()
    try:
        opened = opener.activate(prepared.value, project.request)
        assert opened.is_success, opened.diagnostics
        snapshot = project.services.project_projection.snapshot()
        assert app.install_project_open(token, snapshot, build_paged_slots(prepared.value.records.paged_read))
    finally:
        app.finish_project_open(token)
    full = app.collection
    assert last.id not in app.entry_labels
    context = ExecutionContext(app_context=app)
    desktop_statistics = app.workbench_statistics(full)

    def forbid_range(*args, **kwargs):
        raise AssertionError("Counts must use the paged aggregate or the explicitly selected keys")

    with monkeypatch.context() as patch:
        patch.setattr(type(full), "__iter__", forbid_range)
        patch.setattr(type(full), "query_rows", forbid_range)
        patch.setattr(full, "statistics", forbid_range)
        result = _tool_get_statistics({}, context)
        assert result.success, result.message
        assert result.data["total"] == desktop_statistics.summary.total == len(entries)
        assert result.data["label_distribution"] == {"old": 2}
    catalog = _tool_list_labels({}, context)
    assert catalog.success, catalog.message
    assert {label["id"]: label["count"] for label in catalog.data["labels"]} == {"old": 2, "new": 0}
    context._source_input_full_collection = full
    context.collection = context._target_collection = TranslationEntryCollection((last,))
    with monkeypatch.context() as patch:
        patch.setattr(type(full), "__iter__", forbid_range)
        patch.setattr(type(full), "query_rows", forbid_range)
        result = _tool_get_statistics({}, context)
        assert result.success, result.message
        assert result.data["total"] == 1
        assert result.data["label_distribution"] == {"old": 1}
    narrowed = _tool_list_labels({}, context)
    assert narrowed.success, narrowed.message
    assert {label["id"]: label["count"] for label in narrowed.data["labels"]} == {"old": 1, "new": 0}
    app.set_filter(label=["old"])
    result = _tool_manage_entry_labels({"action": "batch_assign", "name": "待校对"}, context)
    assert result.success, result.message
    assert result.data == {"assigned_count": 1, "filter_total": 1}
    assert app.workbench_statistics(full).labels == {"old": 2, "new": 1}
    observed = {entry.identity: membership for entry, _, membership in full.query_rows() if membership}
    assert observed == {last.identity: {"old", "new"}, previous.identity: {"old"}}
    assert tuple(entry.identity for entry in context.collection) == (last.identity,)
