"""Partial template repair freezes its source range across confirmation and recovery."""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from tests.application.assistant_requests.test_source_input_selection import (
    project as project_fixture,
    repaired as repaired_fixture,
)
from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.assistant_requests.recovery import recover_request
from transbridge.application.assistant_requests.source_input_selection import (
    apply_source_input_selection,
    prepare_source_input_repair,
    readmit_source_input_confirmation,
)
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.smart_assistant.request_scope import apply_captured_scope, execution_version
from transbridge.smart_assistant.tools.types import ExecutionContext
from transbridge.smart_assistant.tools.write_back_preparation import _selected_sources

repaired = repaired_fixture
project = project_fixture


@pytest.fixture
def template(repaired):
    case = repaired
    effect = case.request.effects[0]
    receipt = json.loads(effect.receipt)
    repair = receipt["result"]["data"]["source_input_repair"]
    repair.update(requirement="template", source_keys=["old-selected", "skipped"])
    repair["entry_keys"] = [entry.identity.to_dict() for entry in case.collection]
    case.request = replace(case.request, effects=(replace(effect, receipt=json.dumps(receipt)),))
    case.service.update_request(case.context, case.request.request_id, lambda _old: case.request)
    case.app.slots = {"resolved-selected": SimpleNamespace(collection=case.collection), "skipped": SimpleNamespace()}
    case.source_scope = {
        "requested_source_keys": ["old-selected", "skipped"],
        "selected_source_keys": ["old-selected"],
        "resolved_source_keys": ["resolved-selected"],
    }
    return case


def _prepare(case, **changes):
    return prepare_source_input_repair(
        case.service,
        case.context,
        case.request.request_id,
        case.request.revision,
        changes.get("epoch", case.request.lease_epoch),
        changes.get("items", ("translate",)),
        {},
        source_scope=changes.get("source_scope", case.source_scope),
        view_id="view",
        allowed_tools=("repair_read",),
    )


def _assert_scope(case, request, admission):
    ctx = ExecutionContext(app_context=case.app, assistant_gate=SimpleNamespace(admission=admission))
    apply_captured_scope(request, case.ingress, ctx)
    override = json.loads(execution_version(ctx))["source_input_selection"]
    assert override["source_scope"] == case.source_scope
    assert [key for key, _slot in _selected_sources({"source_keys": ["resolved-selected"]}, ctx)] == [
        "resolved-selected"
    ]
    assert [key for key, _slot in _selected_sources({}, ctx)] == ["resolved-selected"]
    for keys in (["resolved-selected", "skipped"], ["old-selected", "skipped"], ["skipped"]):
        with pytest.raises(RequestError, match="REQUEST_SCOPE_MISMATCH"):
            _selected_sources({"source_keys": keys}, ctx)


def test_partial_template_repair_survives_confirmation_retry_and_restart(template):
    case = template
    selected = _prepare(case)
    _assert_scope(case, selected.request, selected.admission)
    assert case.service.state(case.context)["ingress"] == case.ingress
    assert case.service.scheduler.release(selected.admission)
    renewed = readmit_source_input_confirmation(
        case.service,
        case.context,
        selected.request.request_id,
        selected.request.revision,
        selected.request.lease_epoch,
        ("translate",),
        view_id="view",
        allowed_tools=("repair_read",),
    )
    before = json.loads(selected.request.execution_version_json)["source_input_selection"]
    assert json.loads(renewed.request.execution_version_json)["source_input_selection"] == before
    _assert_scope(case, renewed.request, renewed.admission)
    assert case.service.scheduler.release(renewed.admission)
    recovered = case.service.update_request(case.context, renewed.request.request_id, recover_request)
    fresh = case.service.scheduler.select_next_turn(
        case.context.session_id, "view", (recovered,), automatic=False, allowed_tools=("repair_read",)
    )
    assert fresh is not None
    _assert_scope(case, recovered, fresh.admission)
    with pytest.raises(RequestError, match="REQUEST_SCOPE_MISMATCH"):
        _assert_scope(case, recovered, selected.admission)
    assert case.service.state(case.context)["ingress"] == case.ingress


@pytest.mark.parametrize(
    "change",
    [
        {"selected_source_keys": []},
        {"requested_source_keys": ["old-selected"]},
        {"selected_source_keys": ["outside"]},
        {"resolved_source_keys": []},
        {"resolved_source_keys": ["resolved-selected", "extra"]},
        {"selected_source_keys": ["old-selected", "skipped"], "resolved_source_keys": ["same", "same"]},
    ],
)
def test_empty_or_expanded_template_scope_cannot_change_waiting_request(template, change):
    case = template
    before = case.service.state(case.context)
    with pytest.raises(RequestError, match="REQUEST_SCOPE_MISMATCH"):
        _prepare(case, source_scope={**case.source_scope, **change})
    assert case.service.state(case.context) == before


def test_template_scope_still_requires_matching_failed_receipt_item_and_lease(template):
    case = template
    before = case.service.state(case.context)
    for changes in ({"epoch": 5}, {"items": ("other",)}):
        with pytest.raises(RequestError):
            _prepare(case, **changes)
    assert case.service.state(case.context) == before
    effect = case.request.effects[0]
    receipt = json.loads(effect.receipt)
    receipt["result"]["data"]["source_input_repair"]["request_lease_epoch"] = 5
    changed = replace(case.request, effects=(replace(effect, receipt=json.dumps(receipt)),))
    case.service.update_request(case.context, changed.request_id, lambda _old: changed)
    before = case.service.state(case.context)
    with pytest.raises(RequestError, match="REQUEST_SCOPE_MISMATCH"):
        _prepare(case)
    assert case.service.state(case.context) == before


def test_partial_scope_does_not_apply_to_original_text_repairs(repaired):
    case = repaired
    scope = {
        "requested_source_keys": ["source"],
        "selected_source_keys": ["source"],
        "resolved_source_keys": ["source"],
    }
    with pytest.raises(RequestError, match="REQUEST_SCOPE_MISMATCH"):
        apply_source_input_selection(
            case.service,
            case.context,
            case.request.request_id,
            case.request.revision,
            case.request.lease_epoch,
            ("translate",),
            case.mappings,
            source_scope=scope,
        )


def test_apply_selection_copies_frozen_scope_and_preserves_original_ingress(template):
    case = template
    expected = deepcopy(case.source_scope)
    updated = apply_source_input_selection(
        case.service,
        case.context,
        case.request.request_id,
        case.request.revision,
        case.request.lease_epoch,
        ("translate",),
        {},
        source_scope=case.source_scope,
    )
    case.source_scope["resolved_source_keys"].append("later-mutation")
    assert json.loads(updated.execution_version_json)["source_input_selection"]["source_scope"] == expected
    assert case.service.state(case.context)["ingress"] == case.ingress


def test_template_scope_does_not_remap_ambiguous_entry_ids_from_skipped_sources(template):
    case = template
    effect = case.request.effects[0]
    receipt = json.loads(effect.receipt)
    receipt["result"]["data"]["source_input_repair"]["entry_keys"] = [
        EntryKey(SourceNamespace(namespace), "old-0").to_dict() for namespace in ("first", "skipped")
    ]
    changed = replace(case.request, effects=(replace(effect, receipt=json.dumps(receipt)),))
    case.service.update_request(case.context, changed.request_id, lambda _old: changed)
    selected = _prepare(case)
    _assert_scope(case, selected.request, selected.admission)
    assert case.service.state(case.context)["ingress"] == case.ingress
