"""Real local command adapters retain durable undo evidence for their own effect."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.application.assistant_requests.test_round_undo import _setup
from tests.conftest import make_test_collection
from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.application.io.identity import ExternalEntryRef
from transbridge.application.io.publish import CommitDecision
from transbridge.application.projects import EntryStatePatch
from transbridge.application.projects.assistant_undo import VariantUndoReceipt, build_variant_inverse
from transbridge.application.projects.gui_facade import GuiProjectCommandFacade
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.smart_assistant.request_scope import apply_captured_scope
from transbridge.smart_assistant.tools._project_tool_mutations import ProjectToolTarget
from transbridge.smart_assistant.tools.tool_editor import (
    _tool_edit_translation,
    _tool_manage_entry_labels,
    _tool_set_stage,
)
from transbridge.smart_assistant.tools.tool_migrator import _tool_apply_dictionary
from transbridge.smart_assistant.tools.types import ExecutionContext
from transbridge.smart_assistant.tools.undo_capture import capture_variant_command
from transbridge.translation_memory.manager import TranslationMemoryManager
from transbridge.ui.entry_projection import EntryProjectionUpdate

pytest_plugins = ["tests.application.assistant_requests.test_request_repository"]


def _adapter(composed):
    case = _setup(composed)
    services, requests, context, harness, before, _, execution, undo = case
    requests.undo = undo
    commands = GuiProjectCommandFacade(harness.service, None)
    collection = TranslationEntryCollection(
        TranslationEntry(
            id=entry.entry_key.serialize(),
            key=entry.entry_key.local_key,
            original="source",
            context="context",
            translation=entry.translation,
            stage=entry.stage.value,
            entry_key=entry.entry_key,
            revision=entry.revision,
            external_refs=entry.external_refs,
        )
        for entry in before.entries
    )
    app = SimpleNamespace(
        collection=collection,
        active_slot=SimpleNamespace(collection=collection),
        uses_authoritative_projection=True,
        active_version_identity=(before.ref.project_id.value, before.ref.identity.value),
        project_revision=harness.service.active.project.envelope.revision,
        variant_revision=before.revision,
        project_commands=commands,
        runtime_context=replace(context, run_id="local-command"),
        entry_projection=lambda keys: EntryProjectionUpdate.from_snapshot(
            SimpleNamespace(values=harness.service.active.variant.snapshot().to_dto().envelope.data), keys=keys
        ),
    )
    app.replace_projected_labels = lambda entries, labels, **kwargs: commands.replace_labels(
        entries, labels, app.runtime_context, **kwargs
    )

    def admitted_commit(_effect_id, action):
        action()
        return CommitDecision(True)

    gate = SimpleNamespace(
        service=requests,
        context=context,
        current_request=lambda: next(
            r for r in requests.requests(requests.state(context)) if r.request_id == execution.request_id
        ),
        commit=admitted_commit,
    )
    adapter = ExecutionContext(app_context=app, assistant_gate=gate, assistant_effect_id="effect")
    return case, adapter


@pytest.mark.parametrize("entrypoint", ["entry_states", "labels", "entry_records"])
def test_real_variant_command_entrypoints_persist_scoped_inverse(composed, entrypoint):
    case, context = _adapter(composed)
    _, requests, request_context, harness, before, _, execution, undo = case
    first = next(iter(context.app_context.collection))
    if entrypoint == "entry_states":
        capture_variant_command(
            context,
            lambda: context.project_commands.replace_entry_states(
                {first.identity: ("assistant change", first.stage)},
                context.runtime_context,
                **ProjectToolTarget.capture(context).expected(),
            ),
        )
    elif entrypoint == "labels":
        assert context.commit_label_state(
            {first.identity: {"assistant-label"}}, {"assistant-label": {"name": "Assistant label"}}, before=({}, {})
        ).is_success
    else:
        capture_variant_command(
            context,
            lambda: context.project_commands.replace_entry_records(
                {
                    first.identity: EntryStatePatch(
                        "imported change", first.stage, (ExternalEntryRef("paratranz", "project:1", 99),)
                    )
                },
                context.runtime_context,
                **ProjectToolTarget.capture(context).expected(),
            ),
        )

    current = harness.service.active.variant.snapshot()
    assert current.revision == before.revision + 1
    record = requests.state(request_context)["undo_rounds"][execution.work_round_id]
    assert len(record["commits"]) == 1
    commit = record["commits"][0]
    assert commit["effect_id"] == "effect" and commit["status"] == "recorded"
    receipt = VariantUndoReceipt.from_dict(undo._read(request_context, commit["receipt"]))
    inverse = build_variant_inverse(current, receipt, run_id="inverse")
    assert tuple((e.translation, e.stage, e.labels, e.external_refs) for e in inverse.entries) == tuple(
        (e.translation, e.stage, e.labels, e.external_refs) for e in before.entries
    )
    assert inverse.label_library == before.label_library


@pytest.mark.parametrize("effect_id", ["unregistered", ""])
def test_unregistered_effect_cannot_mutate_through_capture(composed, effect_id):
    _, context = _adapter(composed)
    context.assistant_effect_id = effect_id
    changed = []
    with pytest.raises(RequestError, match="effect"):
        capture_variant_command(context, lambda: changed.append(True))
    assert changed == []


def test_independent_tool_without_assistant_scope_preserves_mutation_result():
    value = object()
    assert capture_variant_command(SimpleNamespace(), lambda: value) is value


def test_production_bootstrap_installs_undo_on_shared_request_service(composed):
    services, requests, _ = composed
    assert requests.undo.requests is requests
    assert requests.undo.projects is services.project_lifecycle


def _project_adapter(composed, current_project_factory):
    case, adapter = _adapter(composed)
    project = current_project_factory(make_test_collection(2))
    case[-1].projects = project.harness.service
    context = ExecutionContext(
        app_context=project.context,
        assistant_gate=adapter.assistant_gate,
        assistant_effect_id="effect",
    )
    apply_captured_scope(SimpleNamespace(source_message_ids=(), execution_version_json=""), (), context)
    return case, context, project


def test_batch_label_records_only_changed_rows_for_round_undo(composed, current_project_factory):
    case, context, project = _project_adapter(composed, current_project_factory)
    _, requests, request_context, _, _, _, execution, undo = case
    first, second = project.context.collection
    library = {"review": {"name": "Review"}}
    assert project.context.replace_projected_labels({first.id: {"review"}}, library).is_success
    result = _tool_manage_entry_labels({"action": "batch_assign", "name": "Review"}, context)
    assert result.success and result.data == {"assigned_count": 1, "filter_total": 2}, result.message
    record = requests.state(request_context)["undo_rounds"][execution.work_round_id]
    (commit,) = record["commits"]
    assert commit["effect_id"] == "effect"
    receipt = VariantUndoReceipt.from_dict(undo._read(request_context, commit["receipt"]))
    inverse = build_variant_inverse(project.aggregate.snapshot(), receipt, run_id="restore-labels")
    assert {state.entry_key: state.labels for state in inverse.entries} == {
        first.identity: ("review",),
        second.identity: (),
    }


def test_dictionary_round_undo_restores_only_newly_filled_entries(
    composed, current_project_factory, tmp_path, monkeypatch
):
    case, context, project = _project_adapter(composed, current_project_factory)
    _, requests, request_context, _, _, _, execution, undo = case
    first, second = project.context.collection
    monkeypatch.setattr(TranslationMemoryManager, "default_dir", lambda self: tmp_path)
    manager = TranslationMemoryManager()
    for entry in (first, second):
        manager.add(entry.key, entry.original, "From dictionary", mod_file_id="Terms")
    manager.save()
    before = project.aggregate.snapshot()

    result = _tool_apply_dictionary({}, context)

    assert result.success and result.data["applied"] == 1, result.message
    assert project.context.collection.get(second.identity).translation == second.translation
    (commit,) = requests.state(request_context)["undo_rounds"][execution.work_round_id]["commits"]
    assert commit["effect_id"] == "effect"
    receipt = VariantUndoReceipt.from_dict(undo._read(request_context, commit["receipt"]))
    assert [state.entry_key for state in receipt.before.entries] == [first.identity]
    inverse = build_variant_inverse(project.aggregate.snapshot(), receipt, run_id="undo-dictionary")
    assert [(e.translation, e.stage) for e in inverse.entries] == [(e.translation, e.stage) for e in before.entries]
    repeated = _tool_apply_dictionary({}, context)
    assert repeated.success and repeated.data["applied"] == 0, repeated.message
    assert requests.state(request_context)["undo_rounds"][execution.work_round_id]["commits"] == [commit]


@pytest.mark.parametrize("failure", ["receipt_store", "finish"])
@pytest.mark.parametrize("action", ["edit", "labels"])
def test_successful_command_survives_undo_receipt_failure_and_next_command_runs(
    composed, current_project_factory, monkeypatch, failure, action
):
    case, context, project = _project_adapter(composed, current_project_factory)
    undo = case[-1]
    before = project.aggregate.snapshot()
    first = next(iter(project.context.collection))
    command_name = "update_entry" if action == "edit" else "replace_labels"
    command = getattr(project.commands, command_name)
    commands = []

    def counted(*args, **kwargs):
        result = command(*args, **kwargs)
        commands.append(result)
        return result

    monkeypatch.setattr(project.commands, command_name, counted)
    if action == "edit":
        stage_command = project.commands.replace_entry_states

        def counted_stage(*args, **kwargs):
            result = stage_command(*args, **kwargs)
            commands.append(result)
            return result

        monkeypatch.setattr(project.commands, "replace_entry_states", counted_stage)
    if failure == "receipt_store":
        store = undo._store
        stored = []

        def fail_receipt(*args, **kwargs):
            stored.append(args)
            if len(stored) == 2:
                raise OSError("undo receipt disk full")
            return store(*args, **kwargs)

        monkeypatch.setattr(undo, "_store", fail_receipt)
    else:
        finish = undo._finish
        finished = []

        def fail_finish(*args, **kwargs):
            finished.append(args)
            if len(finished) == 1:
                raise OSError("undo finish disk full")
            return finish(*args, **kwargs)

        monkeypatch.setattr(undo, "_finish", fail_finish)

    result = (
        _tool_edit_translation({"entry_id": first.key, "new_translation": "committed once"}, context)
        if action == "edit"
        else _tool_manage_entry_labels({"action": "create", "name": "First label"}, context)
    )

    assert result.success, result.message
    assert result.warnings and "撤销回执保存失败" in result.warnings[0]
    assert "disk full" in result.warnings[0]
    assert len(commands) == 1 and commands[0].is_success
    assert project.aggregate.snapshot().revision == before.revision + 1
    assert context.collection is project.context.collection
    assert context._target_variant_revision == project.context.variant_revision
    following = (
        _tool_set_stage({"entry_ids": [first.key], "stage": 3}, context)
        if action == "edit"
        else _tool_manage_entry_labels({"action": "create", "name": "Second label"}, context)
    )
    assert following.success, following.message
    assert not following.warnings
    assert len(commands) == 2
    assert project.aggregate.snapshot().revision == before.revision + 2
    if action == "edit":
        assert project.context.collection.get(first.identity).translation == "committed once"
        assert project.context.collection.get(first.identity).stage == 3
    else:
        assert sorted(value["name"] for value in project.context.label_library.values()) == [
            "First label",
            "Second label",
        ]


@pytest.mark.parametrize("failure", ["before_store", "command"])
@pytest.mark.parametrize("action", ["edit", "labels"])
def test_precommit_undo_or_command_failure_stays_failed(
    composed, current_project_factory, monkeypatch, failure, action
):
    case, context, project = _project_adapter(composed, current_project_factory)
    before = project.aggregate.snapshot()
    first = next(iter(project.context.collection))
    if failure == "before_store":
        monkeypatch.setattr(
            case[-1], "_store", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("intent failed"))
        )
    else:
        failed = OperationResult.failed(DomainError(ErrorCategory.CONFLICT, "REJECTED", "command rejected"))
        command_name = "update_entry" if action == "edit" else "replace_labels"
        monkeypatch.setattr(project.commands, command_name, lambda *_args, **_kwargs: failed)

    result = (
        _tool_edit_translation({"entry_id": first.key, "new_translation": "not committed"}, context)
        if action == "edit"
        else _tool_manage_entry_labels({"action": "create", "name": "Not created"}, context)
    )

    assert not result.success
    assert "intent failed" in result.message if failure == "before_store" else "command rejected" in result.message
    assert not result.warnings
    assert project.aggregate.snapshot() is before
    assert context._target_variant_revision == before.revision
