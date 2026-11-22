"""Immediate assistant edits use local baselines and reuse authoritative UI objects."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.conftest import make_entry, make_test_collection
from tests.smart_assistant.tools import test_source_import_authority as source_fixtures
from transbridge.application.io.identity import EntryKey, ExternalEntryRef, SourceNamespace
from transbridge.application.projections.models import ProjectionSnapshot
from transbridge.application.projects import EntryStatePatch
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.smart_assistant.request_scope import apply_captured_scope
from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.smart_assistant.tools._project_tool_mutations import ProjectToolTarget
from transbridge.smart_assistant.tools.tool_editor import (
    _tool_edit_translation,
    _tool_manage_entry_labels,
    _tool_set_stage,
)
from transbridge.smart_assistant.tools.types import ExecutionContext
from transbridge.ui.context import CollectionSlot
from transbridge.ui.project_rows import ProjectRowsCollection, build_paged_slots

stored_project = source_fixtures.project


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

    def commit(patches):
        return project.context.project_commands.replace_entry_records(
            patches,
            project.context.runtime_context,
            **target.expected(),
        )

    assert commit({second.identity: EntryStatePatch(second.translation, second.stage, (reference,))}).is_success
    before = project.aggregate.snapshot()
    collection = project.context.collection

    result = commit({
        first.identity: EntryStatePatch(first.translation, first.stage, (reference,)),
        third.identity: EntryStatePatch("must not commit", third.stage, ()),
    })
    assert not result.is_success and "external reference conflict" in result.diagnostics[0].message

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


def test_repaired_selection_keeps_exact_keys_across_successive_edits(current_project_factory):
    project = current_project_factory(make_test_collection(3))
    context = ExecutionContext(app_context=project.context)
    first, second, _ = tuple(project.context.collection)
    scoped = TranslationEntryCollection([first])
    context._source_input_full_collection = project.context.collection
    context._target_collection = scoped
    object.__setattr__(context, "collection", scoped)
    object.__setattr__(context, "active_slot", None)
    assert _tool_edit_translation({"entry_id": first.key, "new_translation": "first"}, context).success
    assert _tool_set_stage({"entry_ids": [first.key], "stage": 5}, context).success
    assert len(context.collection) == 1 and context.collection.get(second.identity) is None
    assert (
        context.collection.get(first.identity).snapshot() == project.context.collection.get(first.identity).snapshot()
    )
    assert not _tool_edit_translation({"entry_id": second.key, "new_translation": "outside scope"}, context).success


def test_set_stage_deduplicates_aliases_and_counts_only_real_writes(current_project_factory):
    project = current_project_factory(make_test_collection(2))
    context = ExecutionContext(app_context=project.context)
    first, second = tuple(project.context.collection)
    ids = [first.key, first.identity.serialize(), second.key]
    revision = project.aggregate.snapshot().revision
    for expected_count in (1, 0):
        result = _tool_set_stage({"entry_ids": ids, "stage": second.stage}, context)
        assert result.success and result.data["updated_count"] == expected_count
        assert project.aggregate.snapshot().revision == revision + 1


def test_return_to_original_text_does_not_revalidate_stale_confirmation(current_project_factory):
    project = current_project_factory(make_test_collection(2))
    context = ExecutionContext(app_context=project.context)
    first = next(iter(project.context.collection))
    assert project.context.update_projected_entry(first.identity, translation="temporary").is_success
    assert project.context.update_projected_entry(first.identity, translation=first.translation).is_success
    before = project.aggregate.snapshot()
    result = _tool_edit_translation({"entry_id": first.key, "new_translation": "stale"}, context)
    assert not result.success and project.aggregate.snapshot() is before


def test_paged_editor_reuses_the_bound_collection_without_full_range_reads(stored_project, monkeypatch):
    project = stored_project
    key = source_fixtures.load_source(project).identity
    assert project.services.gui_project_commands.save(project.request).is_success
    opener = project.services.current_project_opener
    prepared = opener.prepare_active(project.request, paged=True)
    assert prepared.is_success, prepared.diagnostics
    app = project.ctx
    token = app.begin_project_open()
    try:
        opened = opener.activate(prepared.value, project.request)
        assert opened.is_success, opened.diagnostics
        snapshot = project.services.project_projection.snapshot()
        assert app.install_project_open(token, snapshot, build_paged_slots(prepared.value.records.paged_read))
    finally:
        app.finish_project_open(token)
    collection = app.collection
    assert isinstance(collection, ProjectRowsCollection)
    projected = []
    app.collection_changed.connect(projected.append)

    def forbid_range(*_args, **_kwargs):
        pytest.fail("An assistant edit must only read the affected entries")

    monkeypatch.setattr(ProjectStore, "iter_entries", forbid_range)
    context = ExecutionContext(app_context=app)
    assert _tool_edit_translation({"entry_id": key.serialize(), "new_translation": "assistant"}, context).success
    assert _tool_set_stage({"entry_ids": [key.serialize()], "stage": 3}, context).success

    assert app.collection is context._target_collection is collection
    assert projected == [collection, collection]
    entry = collection.get(key)
    assert (entry.translation, entry.stage) == ("assistant", 3)


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
    assert "来源登记或命名空间已变化" in result.message
    assert project.aggregate.snapshot() is before


def test_stage_rejects_whole_batch_for_an_ambiguous_alias(current_project_factory):
    first = replace(make_entry("same"), entry_key=EntryKey(SourceNamespace("source:first"), "same"))
    second = replace(make_entry("same"), entry_key=EntryKey(SourceNamespace("source:second"), "same"))
    project = current_project_factory((make_entry("unique"), first, second))
    context = ExecutionContext(app_context=project.context)
    before = project.aggregate.snapshot()
    result = _tool_set_stage({"entry_ids": ["unique", "same"], "stage": 3}, context)
    assert not result.success and "完整 EntryKey" in result.message
    assert project.aggregate.snapshot() is before
    assert _tool_set_stage({"entry_ids": [second.identity.serialize()], "stage": 3}, context).success
    assert project.context.collection.get(first.identity).stage == 0


def test_source_switch_after_commit_cannot_retarget_the_next_edit(current_project_factory, monkeypatch):
    project = current_project_factory(make_test_collection(2), key="first")
    app = project.context
    app.add_slot("second", CollectionSlot("Second", make_test_collection(2)))
    app.activate_slot("first")
    context = ExecutionContext(app_context=app)
    before = project.aggregate.snapshot()
    update = app.update_projected_entry

    def switch_after_commit(*args, **expected):
        result = update(*args, **expected)
        app.activate_slot("second")
        return result

    monkeypatch.setattr(app, "update_projected_entry", switch_after_commit)
    result = _tool_edit_translation({"entry_id": "entry_000", "new_translation": "first source"}, context)
    assert result.success and project.aggregate.snapshot().revision == before.revision + 1
    result = _tool_edit_translation({"entry_id": "entry_000", "new_translation": "wrong source"}, context)
    assert not result.success and project.aggregate.snapshot().revision == before.revision + 1
