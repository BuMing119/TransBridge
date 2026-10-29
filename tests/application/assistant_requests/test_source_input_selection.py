"""A confirmed legacy mapping renews selection without changing accepted input."""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from tests.application.projects.test_source_update import _xml, project as project_fixture
from transbridge.application.assistant_requests.admission import record_effect_outcome
from transbridge.application.assistant_requests.models import (
    AssistantExecutionRef,
    EffectIntent,
    EffectStatus,
    ItemKind,
    RequestError,
    RequestItem,
    UserRequest,
)
from transbridge.application.assistant_requests.source_input_selection import (
    prepare_source_input_repair,
    readmit_source_input_confirmation,
)
from transbridge.application.contracts import DomainError
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.persistence.v2.variant import SourceFingerprint
from transbridge.smart_assistant.request_execution import RequestExecutionGate
from transbridge.smart_assistant.request_scope import apply_captured_scope, execution_version
from transbridge.smart_assistant.tool_registry import ToolRegistry, ToolSpec
from transbridge.smart_assistant.tools.types import ExecutionContext

project = project_fixture


@pytest.fixture
def repaired(project, monkeypatch, request):
    selected_ids = getattr(request, "param", None)
    services, _updates, context, source_path, project_path, source_id = project
    active = services.project_lifecycle.active
    snapshot = active.variant.snapshot()
    old_keys = tuple(EntryKey(SourceNamespace.legacy(), f"old-{index}") for index in range(3))
    legacy = replace(
        snapshot,
        source_fingerprints=(SourceFingerprint(SourceNamespace.legacy(), None),),
        entries=tuple(replace(row, entry_key=key) for row, key in zip(snapshot.entries, old_keys, strict=True)),
    )
    services.variants.save(legacy.ref, legacy.to_dto())
    for source in active.project.envelope.data["sources"]:
        source.pop("source_artifact_id", None)
        source["location"] = ""
    services.projects.save(active.project_ref, active.project)
    assert services.current_project_opener.open_path(project_path, context).is_success
    identity = (active.project_ref.identity.value, active.formal_variant_ref.identity.value)
    context = replace(context, project_id=identity[0], variant_id=identity[1])
    assert services.gui_session_commands.create_and_activate("repair", context).is_success
    context = replace(
        context,
        project_id=identity[0],
        variant_id=identity[1],
        session_id=services.session_lifecycle.active.aggregate.ref.identity.value,
    )
    service = services.gui_session_commands.assistant_requests
    selection = {
        "selected_entry_ids": [key.local_key for key in old_keys[:2]] if selected_ids is None else selected_ids,
        "filter_state": {"search_query": "keep"},
        "translation_scope": {"stages": [5]},
    }
    accepted = service.accept_input(context, "translate selected", selection=selection)
    scope = tuple(
        (field, str(getattr(context, field))) for field in ("owner_id", "session_id", "project_id", "variant_id")
    )
    request = UserRequest(
        "repair-request",
        context.session_id,
        "translate selected",
        (
            RequestItem("translate", "translate", ItemKind.EXECUTION),
            RequestItem("other", "unrelated", ItemKind.EXECUTION),
        ),
        source_message_ids=(accepted["message_id"],),
        scope=scope,
        execution_version_json=json.dumps({"active_version_identity": identity}),
    )
    execution = AssistantExecutionRef(
        request.request_id, request.revision, ("translate",), "attempt", "dispatch", "turn", context.session_id
    )
    request = replace(request, effects=(EffectIntent("missing", "operation", execution, 0),))
    repair = {
        "kind": "source_input",
        "requirement": "originals",
        "source_keys": [source_id],
        "entry_keys": [key.to_dict() for key in old_keys[:2]],
        "project_id": context.project_id,
        "variant_id": context.variant_id,
    }
    receipt = json.dumps({
        "result": {"success": False, "error_code": "SOURCE_INPUT_REQUIRED", "data": {"source_input_repair": repair}}
    })
    request = record_effect_outcome(request, "missing", status=EffectStatus.FAILED, receipt=receipt)
    service.transact(context, lambda state: state.update(requests=[request.to_dict()]))
    original_ingress = deepcopy(service.state(context)["ingress"])
    replacement = source_path.with_name("chosen.xml")
    _xml(replacement, [(9, "Chosen original"), (10, "Extra baseline row")])
    supplements = services.project_source_supplements
    prepared = supplements.prepare(project_path, source_id, SourceNamespace.legacy().value, str(replacement), context)
    assert prepared.is_success, prepared.diagnostics
    preview = prepared.value
    mapped = preview.choices[0].key
    mappings = {old_keys[0]: mapped, old_keys[1]: None, old_keys[2]: preview.choices[1].key}
    assert supplements.remap(preview.token, mappings, context).is_success
    assert supplements.commit(preview.token, context).is_success
    opened = services.current_project_opener.prepare_path(project_path, context)
    hydration = opened.value.hydrations[0]
    collection = TranslationEntryCollection(
        TranslationEntry(
            item.legacy_id,
            item.entry_key.local_key,
            item.original,
            item.translation,
            item.stage,
            item.context,
            entry_key=item.entry_key,
        )
        for item in hydration.entries
    )
    app = SimpleNamespace(collection=collection, active_version_identity=identity)
    service.scheduler.activate(context.session_id, "view")
    monkeypatch.setattr(ToolRegistry, "_namespaced_tools", {"default": {}})
    ToolRegistry.register(ToolSpec("repair_read", "read", "read", {}))
    return SimpleNamespace(
        service=service,
        context=context,
        request=request,
        mappings=mappings,
        mapped=mapped,
        ingress=original_ingress,
        app=app,
        collection=collection,
    )


def _prepare(case, **changes):
    return prepare_source_input_repair(
        case.service,
        changes.get("context", case.context),
        case.request.request_id,
        changes.get("revision", case.request.revision),
        changes.get("epoch", case.request.lease_epoch),
        changes.get("items", ("translate",)),
        changes.get("mappings", case.mappings),
        view_id="view",
        allowed_tools=("repair_read",),
    )


def test_real_legacy_supplement_reprepares_only_mapped_selection_under_a_new_lease(repaired):
    case = repaired
    before = ExecutionContext(app_context=case.app)
    with pytest.raises(RequestError, match="REQUEST_SCOPE_MISMATCH"):
        apply_captured_scope(case.request, case.ingress, before)
    selected = _prepare(case)
    assert selected.admission.request_lease_epoch == case.request.lease_epoch + 1
    assert case.service.state(case.context)["ingress"] == case.ingress
    assert selected.request.items[1].waiting_reasons == ()  # Temporary scheduler fencing was not persisted.
    gate = RequestExecutionGate(case.service, case.context, selected.admission)
    ctx = ExecutionContext(app_context=case.app, assistant_gate=gate)
    gate.prepare_steps([{"tool": "repair_read", "args": {}}])
    gate.before("repair_read", {}, ctx)
    assert {entry.identity for entry in ctx.collection} == {case.mapped}
    assert {entry.identity for entry in ctx.selected_entries} == {case.mapped}
    assert ctx.filter_state == {"search_query": "keep"} and ctx.translation_scope == {"stages": [5]}
    assert "source_input_selection" in json.loads(execution_version(ctx))
    assert len(case.collection) == 2  # The additional real baseline row is not admitted.


@pytest.mark.parametrize("change", ["revision", "epoch", "items", "context", "mappings"])
def test_stale_or_unrelated_repair_does_not_change_request_or_ingress(repaired, change):
    case = repaired
    options = {
        "revision": 2,
        "epoch": 1,
        "items": ("other",),
        "context": replace(case.context, variant_id="different"),
        "mappings": {EntryKey(SourceNamespace("unrelated"), "row"): case.mapped},
    }
    before = case.service.state(case.context)
    with pytest.raises((RequestError, DomainError)):
        _prepare(case, **{change: options[change]})
    assert case.service.state(case.context) == before


def test_busy_or_failed_reproposal_keeps_original_waiting_request_retryable(repaired, monkeypatch):
    case = repaired
    blocking = replace(
        case.request, request_id="blocking", effects=(), items=(replace(case.request.items[1], waiting_reasons=()),)
    )
    occupied = case.service.scheduler.select_next_turn(case.context.session_id, "view", (blocking,))
    before = case.service.state(case.context)
    with pytest.raises(RequestError, match="SOURCE_INPUT_REPAIR_BUSY"):
        _prepare(case)
    assert case.service.state(case.context) == before
    case.service.scheduler.release(occupied.admission)
    update = case.service.update_request
    with monkeypatch.context() as fail:
        fail.setattr(
            case.service, "update_request", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full"))
        )
        with pytest.raises(OSError, match="disk full"):
            _prepare(case)
    assert case.service.state(case.context) == before
    assert case.service.update_request == update
    assert _prepare(case).admission.request_lease_epoch == 1


def test_override_cannot_be_used_by_an_unrelated_item_or_changed_version(repaired):
    case = repaired
    selected = _prepare(case)
    for different_item in (False, True):
        admission = replace(selected.admission, ready_item_ids=("other",)) if different_item else selected.admission
        gate = SimpleNamespace(admission=admission)
        app = (
            case.app
            if different_item
            else SimpleNamespace(collection=case.collection, active_version_identity=("other", "v"))
        )
        ctx = ExecutionContext(app_context=app, assistant_gate=gate)
        with pytest.raises(RequestError, match="REQUEST_SCOPE_MISMATCH"):
            apply_captured_scope(selected.request, case.ingress, ctx)


@pytest.mark.parametrize("repaired", [[]], indirect=True)
def test_empty_manual_selection_uses_actual_failed_operation_targets(repaired):
    case = repaired
    selected = _prepare(case)
    gate = RequestExecutionGate(case.service, case.context, selected.admission)
    ctx = ExecutionContext(app_context=case.app, assistant_gate=gate)
    gate.prepare_steps([{"tool": "repair_read", "args": {}}])
    gate.before("repair_read", {}, ctx)
    assert {entry.identity for entry in ctx.collection} == {case.mapped}
    assert case.service.state(case.context)["ingress"] == case.ingress


@pytest.mark.parametrize("repaired", [[]], indirect=True)
def test_template_repair_without_changed_entry_identity_needs_no_entry_mapping(repaired):
    case = repaired
    effect = case.request.effects[0]
    receipt = json.loads(effect.receipt)
    repair = receipt["result"]["data"]["source_input_repair"]
    repair["requirement"] = "template"
    repair["entry_keys"] = [entry.identity.to_dict() for entry in case.collection]
    request = replace(case.request, effects=(replace(effect, receipt=json.dumps(receipt)),))
    case.service.update_request(case.context, request.request_id, lambda _old: request)
    selected = _prepare(case, mappings={})
    gate = RequestExecutionGate(case.service, case.context, selected.admission)
    ctx = ExecutionContext(app_context=case.app, assistant_gate=gate)
    gate.before("repair_read", {}, ctx)
    assert {entry.identity for entry in ctx.collection} == {entry.identity for entry in case.collection}
    assert case.service.state(case.context)["ingress"] == case.ingress


def test_repair_override_cannot_reuse_an_old_confirmation_lease(repaired):
    case = repaired
    selected = _prepare(case)
    gate = SimpleNamespace(admission=replace(selected.admission, request_lease_epoch=case.request.lease_epoch))
    ctx = ExecutionContext(app_context=case.app, assistant_gate=gate)
    with pytest.raises(RequestError, match="REQUEST_SCOPE_MISMATCH"):
        apply_captured_scope(selected.request, case.ingress, ctx)


def test_failed_confirmation_delivery_can_readmit_only_the_same_saved_mapping(repaired, monkeypatch):
    case = repaired
    selected = _prepare(case)
    assert case.service.scheduler.release(selected.admission)
    original = case.service.state(case.context)

    def retry(**options):
        return readmit_source_input_confirmation(
            case.service,
            case.context,
            selected.request.request_id,
            selected.request.revision,
            options.get("epoch", selected.request.lease_epoch),
            options.get("items", ("translate",)),
            view_id="view",
            allowed_tools=("repair_read",),
        )

    for values in ({"epoch": case.request.lease_epoch}, {"items": ("other",)}):
        with pytest.raises(RequestError):
            retry(**values)
    assert case.service.state(case.context) == original
    with monkeypatch.context() as fail:
        fail.setattr(
            case.service, "update_request", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full"))
        )
        with pytest.raises(OSError, match="disk full"):
            retry()
    assert case.service.state(case.context) == original
    renewed = retry()
    assert renewed.admission.request_lease_epoch == selected.admission.request_lease_epoch + 1
    before = json.loads(selected.request.execution_version_json)["source_input_selection"]
    after = json.loads(renewed.request.execution_version_json)["source_input_selection"]
    assert before == after
    assert case.service.state(case.context)["ingress"] == case.ingress


def test_saved_mapping_survives_pause_resume_but_old_execution_lease_does_not(repaired):
    from transbridge.application.assistant_requests.recovery import recover_request

    case = repaired
    selected = _prepare(case)
    case.service.scheduler.release(selected.admission)
    paused = case.service.command(case.context, selected.request.request_id, "pause", selected.request.revision)
    resumed = case.service.command(case.context, paused.request_id, "resume", paused.revision)
    assert resumed.revision == selected.request.revision
    # Real restart recovery rotates execution authority without changing the human mapping.
    resumed = case.service.update_request(case.context, resumed.request_id, recover_request)
    fresh = case.service.scheduler.select_next_turn(
        case.context.session_id, "view", (resumed,), automatic=False, allowed_tools=("repair_read",)
    )
    assert fresh is not None and fresh.admission.request_lease_epoch != selected.admission.request_lease_epoch
    gate = RequestExecutionGate(case.service, case.context, fresh.admission)
    ctx = ExecutionContext(app_context=case.app, assistant_gate=gate)
    gate.prepare_steps([{"tool": "repair_read", "args": {}}])
    gate.before("repair_read", {}, ctx)
    assert {entry.identity for entry in ctx.collection} == {case.mapped}
    stale = ExecutionContext(app_context=case.app, assistant_gate=SimpleNamespace(admission=selected.admission))
    with pytest.raises(RequestError, match="REQUEST_SCOPE_MISMATCH"):
        apply_captured_scope(resumed, case.ingress, stale)


def test_full_mapped_entry_identity_prevents_another_source_with_the_same_local_key(repaired):
    case = repaired
    selected = _prepare(case)
    actual = next(entry for entry in case.collection if entry.identity == case.mapped)
    unrelated_key = replace(actual.identity, namespace=SourceNamespace("unrelated:source"))
    wrong = replace(actual, entry_key=unrelated_key)
    app = SimpleNamespace(
        collection=TranslationEntryCollection((wrong,)), active_version_identity=case.app.active_version_identity
    )
    ctx = ExecutionContext(app_context=app, assistant_gate=SimpleNamespace(admission=selected.admission))
    with pytest.raises(RequestError, match="REQUEST_SCOPE_MISMATCH"):
        apply_captured_scope(selected.request, case.ingress, ctx)
