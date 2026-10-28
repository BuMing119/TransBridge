"""Immediate assistant edits use local baselines and reuse authoritative UI objects."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.conftest import make_entry, make_test_collection
from tests.smart_assistant.tools.test_upload_cancellation_receipt import _context
from tests.smart_assistant.tools.test_upload_result_lifecycle import Remote
from transbridge.application.io.identity import EntryKey, ExternalEntryRef, SourceNamespace
from transbridge.application.projections.models import ProjectionSnapshot
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.smart_assistant.request_scope import apply_captured_scope
from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.smart_assistant.tools._entry_upload import upload_entries
from transbridge.smart_assistant.tools._project_tool_mutations import ProjectToolTarget
from transbridge.smart_assistant.tools.tool_editor import (
    _tool_edit_translation,
    _tool_manage_entry_labels,
    _tool_set_stage,
)
from transbridge.smart_assistant.tools.types import ExecutionContext
from transbridge.ui.context import CollectionSlot


def _forbid_snapshot_serialization(monkeypatch):
    def forbidden(_snapshot):
        raise AssertionError("an immediate edit must read immutable projection values directly")

    monkeypatch.setattr(ProjectionSnapshot, "to_dict", forbidden)


@pytest.mark.parametrize("action", ["edit", "stage"])
def test_single_entry_command_reuses_unchanged_objects_and_builds_one_collection(
    current_project_factory, monkeypatch, action
):
    project = current_project_factory(make_test_collection(3))
    first, second, third = tuple(project.context.collection)
    _forbid_snapshot_serialization(monkeypatch)
    built = []
    bulk_load = TranslationEntryCollection._bulk_load

    def track(collection, entries):
        built.append(collection)
        return bulk_load(collection, entries)

    monkeypatch.setattr(TranslationEntryCollection, "_bulk_load", track)
    context = ExecutionContext(app_context=project.context)
    result = (
        _tool_edit_translation({"entry_id": first.key, "new_translation": "edited"}, context)
        if action == "edit"
        else _tool_set_stage({"entry_ids": [first.key], "stage": 3}, context)
    )

    assert result.success, result.message
    current = project.context.collection
    assert current.get(second.identity) is second
    assert current.get(third.identity) is third
    assert len(built) == 1
    assert current.get(first.identity).revision == project.aggregate.snapshot().entries[0].revision
    assert not project.context.authoritative_projection_diverged()


@pytest.mark.parametrize("same_entry", [False, True])
def test_captured_editor_scope_allows_unrelated_edit_and_rejects_target_conflict(current_project_factory, same_entry):
    project = current_project_factory(make_test_collection(3))
    context = ExecutionContext(app_context=project.context)
    first, second, _ = tuple(project.context.collection)
    apply_captured_scope(SimpleNamespace(source_message_ids=(), execution_version_json=""), (), context)
    changed = first if same_entry else second
    assert project.context.update_projected_entry(changed.identity, translation="latest").is_success
    preserved = project.context.collection.get(changed.identity)

    result = _tool_edit_translation({"entry_id": first.key, "new_translation": "assistant"}, context)

    assert result.success is not same_entry, result.message
    assert project.context.collection.get(changed.identity) is preserved
    assert project.context.collection.get(changed.identity).translation == "latest"
    assert project.context.collection.get(first.identity).translation == ("latest" if same_entry else "assistant")
    assert not project.context.authoritative_projection_diverged()


def test_external_reference_conflict_rejects_entire_command_before_commit(current_project_factory):
    project = current_project_factory(make_test_collection(3))
    first, second, third = tuple(project.context.collection)
    reference = ExternalEntryRef("paratranz", "project:7", 701)
    target = ProjectToolTarget.capture(ExecutionContext(app_context=project.context))
    target.commit_records((replace(second, external_refs=(reference,)),))
    before = project.aggregate.snapshot()
    collection = project.context.collection

    with pytest.raises(ValueError, match="external reference conflict"):
        target.commit_records((
            replace(first, external_refs=(reference,)),
            replace(third, translation="must not commit"),
        ))

    assert project.aggregate.snapshot() is before
    assert project.context.collection is collection
    assert project.context.collection.get(third.identity) is third


def test_queued_target_change_is_detected_from_the_prepared_entry_baseline(current_project_factory, monkeypatch):
    project = current_project_factory(make_test_collection(2))
    first = next(iter(project.context.collection))
    context = ExecutionContext(app_context=project.context)
    dispatch = context.safe_mutate_wait

    def edit_then_commit(action, **options):
        assert project.context.update_projected_entry(first.identity, translation="concurrent").is_success
        return dispatch(action, **options)

    monkeypatch.setattr(context, "safe_mutate_wait", edit_then_commit)
    result = _tool_set_stage({"entry_ids": [first.key], "stage": 3}, context)

    assert not result.success
    assert project.context.collection.get(first.identity).translation == "concurrent"
    assert project.context.collection.get(first.identity).stage == first.stage


def test_captured_editor_scope_advances_only_after_its_own_successful_edit(current_project_factory):
    project = current_project_factory(make_test_collection(2))
    first = next(iter(project.context.collection))
    context = ExecutionContext(app_context=project.context)
    apply_captured_scope(SimpleNamespace(source_message_ids=(), execution_version_json=""), (), context)

    assert _tool_edit_translation({"entry_id": first.key, "new_translation": "assistant"}, context).success
    assert _tool_set_stage({"entry_ids": [first.key], "stage": 3}, context).success

    current = project.context.collection.get(first.identity)
    assert (current.translation, current.stage) == ("assistant", 3)
    assert context.collection is project.context.collection


def test_in_place_unsubmitted_edit_is_not_overwritten(current_project_factory, monkeypatch):
    project = current_project_factory(make_test_collection(2))
    first = next(iter(project.context.collection))
    context = ExecutionContext(app_context=project.context)
    dispatch = context.safe_mutate_wait

    def edit_then_commit(action, **options):
        with pytest.warns(DeprecationWarning):
            first.translation = "unsubmitted"
        return dispatch(action, **options)

    monkeypatch.setattr(context, "safe_mutate_wait", edit_then_commit)
    result = _tool_edit_translation({"entry_id": first.key, "new_translation": "assistant"}, context)

    assert not result.success
    assert project.context.collection.get(first.identity).translation == "unsubmitted"
    assert project.aggregate.snapshot().entries[0].translation == ""


@pytest.mark.parametrize("conflict", ["translation", "labels", "library"])
def test_label_command_uses_preparation_baseline_and_current_cas(current_project_factory, monkeypatch, conflict):
    project = current_project_factory(make_test_collection(2))
    first = next(iter(project.context.collection))
    context = ExecutionContext(app_context=project.context)
    dispatch = context.safe_mutate_wait

    def edit_then_commit(action, **options):
        if conflict == "translation":
            assert project.context.update_projected_entry(first.identity, translation="latest").is_success
        else:
            labels = {first.key: {"other"}} if conflict == "labels" else {}
            library = {"other": {"name": "Other"}}
            assert project.context.replace_projected_labels(labels, library).is_success
        return dispatch(action, **options)

    monkeypatch.setattr(context, "safe_mutate_wait", edit_then_commit)
    result = _tool_manage_entry_labels({"action": "create", "name": "Assistant"}, context)

    assert result.success is (conflict == "translation"), result.message
    if conflict == "translation":
        assert project.context.collection.get(first.identity).translation == "latest"
        assert [value["name"] for value in project.context.label_library.values()] == ["Assistant"]
    else:
        assert "标签状态或标签库已变化" in result.message
        assert project.context.label_library == {"other": {"name": "Other"}}


def test_upload_association_builds_one_collection_and_reuses_untouched_entry(
    current_project_factory, tmp_path, monkeypatch
):
    context, project = _context(current_project_factory, tmp_path, monkeypatch)
    first, second, third = tuple(project.context.collection)
    _forbid_snapshot_serialization(monkeypatch)
    built = []
    bulk_load = TranslationEntryCollection._bulk_load

    def track(collection, entries):
        built.append(collection)
        return bulk_load(collection, entries)

    monkeypatch.setattr(TranslationEntryCollection, "_bulk_load", track)
    result = upload_entries({"entry_ids": [first.key]}, context, project.context.collection, Remote(), 7, None)

    assert result.success, result.message
    assert len(built) == 1
    assert project.context.collection.get(second.identity) is second
    assert project.context.collection.get(third.identity) is third
    assert project.context.collection.get(first.identity).external_refs == (
        ExternalEntryRef("paratranz", "project:7", 701),
    )


def test_upload_reference_conflict_preserves_remote_receipt_without_local_partial_commit(
    current_project_factory, tmp_path, monkeypatch
):
    context, project = _context(current_project_factory, tmp_path, monkeypatch)
    first, second, _ = tuple(project.context.collection)
    reference = ExternalEntryRef("paratranz", "project:7", 701)
    ProjectToolTarget.capture(context).commit_records((replace(second, external_refs=(reference,)),))
    before = project.aggregate.snapshot()
    collection = project.context.collection

    result = upload_entries({"entry_ids": [first.key]}, context, collection, Remote(), 7, None)

    assert not result.success and result.partial
    assert result.data["uploaded"] == 1
    assert result.data["durable"]
    assert result.data["upload_result"]["receipts"][0]["outcome"] == "confirmed"
    assert result.data["upload_result"]["receipts"][0]["association"] == "deferred"
    assert "external reference conflict" in result.data["deferred_associations"][0]["error"]
    assert project.aggregate.snapshot() is before
    assert project.context.collection is collection


def test_registered_label_assignment_uses_captured_collection_without_third_argument(current_project_factory):
    project = current_project_factory(make_test_collection(2), label_library={"review": {"name": "Review"}})
    first = next(iter(project.context.collection))
    context = ExecutionContext(app_context=project.context)
    apply_captured_scope(SimpleNamespace(source_message_ids=(), execution_version_json=""), (), context)
    tool = ToolRegistry.get("manage_entry_labels")

    result = tool.execute({"action": "assign", "name": "Review", "entry_ids": [first.key]}, context)

    assert result.success, result.message
    assert project.context.entry_labels[first.id] == {"review"}
    assert project.aggregate.snapshot().entries[0].labels == ("review",)


@pytest.mark.parametrize("action", ["assign", "unassign", "batch_assign"])
@pytest.mark.parametrize("switch_before_call", [False, True])
def test_registered_label_assignment_never_retargets_equal_local_key_after_source_switch(
    current_project_factory, monkeypatch, action, switch_before_call
):
    first = replace(make_entry("shared"), entry_key=EntryKey(SourceNamespace("source:first"), "shared"))
    second = replace(make_entry("shared"), entry_key=EntryKey(SourceNamespace("source:second"), "shared"))
    labels = {entry.identity: {"review"} for entry in (first, second)} if action == "unassign" else {}
    project = current_project_factory(
        (first, second), key="first", labels=labels, label_library={"review": {"name": "Review"}}
    )
    app = project.context
    app.active_slot.collection = TranslationEntryCollection((first,))
    app.add_slot("second", CollectionSlot("Second", TranslationEntryCollection((second,))))
    app.activate_slot("first")
    context = ExecutionContext(app_context=app)
    apply_captured_scope(SimpleNamespace(source_message_ids=(), execution_version_json=""), (), context)
    before = project.aggregate.snapshot()
    if switch_before_call:
        app.activate_slot("second")
    else:
        dispatch = context.safe_mutate_wait

        def switch_then_commit(mutation, **options):
            app.activate_slot("second")
            return dispatch(mutation, **options)

        monkeypatch.setattr(context, "safe_mutate_wait", switch_then_commit)
    tool = ToolRegistry.get("manage_entry_labels")

    result = tool.execute({"action": action, "name": "Review", "entry_ids": ["shared"]}, context)

    assert not result.success
    assert "活动来源已变化" in result.message
    assert project.aggregate.snapshot() is before
    assert app.active_key == "second"


def test_editor_rejects_reinstalled_source_with_new_exact_entry_identity(current_project_factory):
    project = current_project_factory(make_test_collection(2))
    context = ExecutionContext(app_context=project.context)
    entries = tuple(
        replace(entry, entry_key=EntryKey(SourceNamespace("source:replacement"), entry.key))
        for entry in make_test_collection(2)
    )
    project.install(entries)
    before = project.aggregate.snapshot()

    result = _tool_edit_translation({"entry_id": entries[0].key, "new_translation": "wrong source"}, context)

    assert not result.success
    assert "不属于捕获的来源" in result.message
    assert project.aggregate.snapshot() is before
