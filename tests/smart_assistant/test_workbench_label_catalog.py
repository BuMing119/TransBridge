"""Compare the label directory returned by the assistant with workbench filter buttons."""

from dataclasses import replace
from types import SimpleNamespace

from tests.conftest import make_test_collection
from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.smart_assistant.tools import register_all
from transbridge.ui.workbench.filters_view import FiltersView
from transbridge.ui.workbench.step2 import Step2PreviewWidget


def test_label_catalog_and_workbench_buttons_count_only_current_source(current_project_factory):
    project = current_project_factory(make_test_collection(2))
    collection = project.context.collection
    first, second = collection
    library = {"red": {"name": "待校对", "color": "#ee0011"}, "blue": {"name": "已检查", "color": "#0022ee"}}
    memberships = {first.id: {"red"}, second.id: set(), "other-source": {"red", "blue"}}
    context = SimpleNamespace(collection=collection, entry_labels=memberships, label_library=library)
    view = FiltersView(on_changed=lambda: None, on_manage_labels=lambda: None)
    try:
        # The table retains a selected filter even when this source has no matching entries.
        view.label_filters.add("blue")
        view.build_labels(tuple(collection), library, memberships)
        register_all()
        result = ToolRegistry.get("list_labels").execute({}, context)
        assert result.success, result.message
        labels = result.data["labels"]
        assert labels == [
            {"id": "red", "name": "待校对", "color": "#ee0011", "count": 1},
            {"id": "blue", "name": "已检查", "color": "#0022ee", "count": 0},
        ]
        assert [(button.toolTip(), button.property("tbUserColor")) for button in view._label_buttons] == [
            (f"● {label['name']} {label['count']}", label["color"]) for label in labels
        ]
        assert view.label_container.itemAt(0).widget().toolTip() == "全部 1"
        view.build_labels(tuple(collection), {}, memberships)
        context.label_library = {}
        assert ToolRegistry.get("list_labels").execute({}, context).data == {"labels": []}
        assert not view._label_buttons
    finally:
        view.close()


def test_two_entries_with_same_display_id_keep_individual_label_counts(current_project_factory):
    first, second = (replace(entry, id="repeated") for entry in make_test_collection(2))
    library = {"red": {"name": "待校对", "color": "#ff0000"}, "blue": {"name": "已检查", "color": "#0000ff"}}
    project = current_project_factory(
        [first, second], labels={first.identity: {"red"}, second.identity: {"blue"}}, label_library=library
    )
    widget = Step2PreviewWidget(project.context)
    try:
        widget.refresh(project.context.collection)
        register_all()
        result = ToolRegistry.get("list_labels").execute({}, project.context)
        assert result.success, result.message
        assert {label["id"]: label["count"] for label in result.data["labels"]} == {"red": 1, "blue": 1}
        assert {button.toolTip() for button in widget._filters_view._label_buttons} == {"● 待校对 1", "● 已检查 1"}
        assert widget._count_lbl.text() == "有标签 2 条 | 共 2 条"
    finally:
        widget.close()
