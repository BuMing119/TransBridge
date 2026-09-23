"""Real local command adapters retain durable undo evidence for their own effect."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.application.assistant_requests.test_round_undo import _setup
from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.io.identity import ExternalEntryRef
from transbridge.application.io.publish import CommitDecision
from transbridge.application.projects.assistant_undo import VariantUndoReceipt, build_variant_inverse
from transbridge.application.projects.gui_facade import GuiProjectCommandFacade
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.smart_assistant.tools._project_tool_mutations import ProjectToolTarget
from transbridge.smart_assistant.tools.types import ExecutionContext
from transbridge.smart_assistant.tools.undo_capture import capture_variant_command

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
        _project_projection=SimpleNamespace(
            snapshot=lambda: SimpleNamespace(
                to_dict=lambda: {"values": harness.service.active.variant.snapshot().to_dto().envelope.data}
            )
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
        first.translation = "assistant change"
        context.publish_collection_modified()
    elif entrypoint == "labels":
        assert context.commit_label_state(
            {first.identity: {"assistant-label"}}, {"assistant-label": {"name": "Assistant label"}}
        ).is_success
    else:
        ProjectToolTarget.capture(context).commit_records((
            replace(
                first, translation="imported change", external_refs=(ExternalEntryRef("paratranz", "project:1", 99),)
            ),
        ))

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
