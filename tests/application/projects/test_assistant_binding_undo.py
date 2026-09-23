"""Local binding inverses preserve Variant edits and never invoke a remote API."""

from dataclasses import replace
import json

import pytest

from tests.application.projects.test_assistant_undo import _case, _service
from tests.application.projects.test_remote_binding import _binding
from transbridge.application.contracts import DomainError, RequestContext
from transbridge.application.projects.assistant_binding_undo import (
    apply_binding_undo,
    capture_binding_undo,
    combine_binding_undo,
    preflight_binding_undo,
)
from transbridge.application.projects.remote_binding import ProjectRemoteBindingService, project_with_paratranz_binding


def _prepared():
    _, _, variant = _case()
    harness = _service(variant, persisted_revision=3)
    lifecycle = harness.service
    before = lifecycle.active.project
    context = RequestContext("owner", project_id=before.envelope.identity, run_id="bind")
    bindings = ProjectRemoteBindingService(lifecycle)
    assert bindings.set_paratranz_binding(_binding(), context, expected_project_revision=0).is_success
    return harness, context, before, lifecycle.active.project


def test_binding_undo_clears_new_local_binding_persistently_without_rewinding_variant():
    harness, context, before, after = _prepared()
    variant = harness.service.active.variant.snapshot()
    receipt = json.loads(json.dumps(capture_binding_undo(before, after)))
    result = apply_binding_undo(harness.service, receipt, replace(context, run_id="undo"))
    assert result.is_success
    assert harness.service.active.project.envelope.revision == 2
    assert harness.service.active.project.envelope.data == before.envelope.data
    assert harness.service.active.persisted_project_revision == 2
    assert harness.service.active.variant.snapshot() == variant and harness.service.active.dirty
    assert harness.store.saved_revisions == [1, 2]


def test_binding_chain_restores_original_binding_in_one_increment():
    harness, context, before, first = _prepared()
    bindings = ProjectRemoteBindingService(harness.service)
    next_binding = replace(_binding(), project_id=99)
    assert bindings.set_paratranz_binding(next_binding, context, expected_project_revision=1).is_success
    second = harness.service.active.project
    combined = combine_binding_undo((capture_binding_undo(before, first), capture_binding_undo(first, second)))
    assert apply_binding_undo(harness.service, combined, context).is_success
    assert harness.service.active.project.envelope.revision == 3
    assert harness.service.active.project.envelope.data == before.envelope.data


def test_binding_aba_or_unrelated_project_revision_change_refuses_undo():
    harness, context, before, after = _prepared()
    bindings = ProjectRemoteBindingService(harness.service)
    assert bindings.clear_paratranz_binding(context, expected_project_revision=1).is_success
    assert bindings.set_paratranz_binding(_binding(), context, expected_project_revision=2).is_success
    with pytest.raises(DomainError, match="changed after"):
        preflight_binding_undo(harness.service, capture_binding_undo(before, after))


def test_capture_cannot_claim_an_unrelated_project_field_or_skipped_revision():
    _, _, before, after = _prepared()
    changed = replace(after, envelope=replace(after.envelope, data={**after.envelope.data, "name": "other"}))
    with pytest.raises(DomainError, match="beyond"):
        capture_binding_undo(before, changed)
    with pytest.raises(DomainError, match="exactly one"):
        capture_binding_undo(before, replace(after, envelope=replace(after.envelope, revision=2)))


def test_binding_inverse_can_restore_previous_target_preserving_other_providers():
    harness, context, _, after = _prepared()
    data = {**after.envelope.data, "remote_bindings": {**after.envelope.data["remote_bindings"], "other": {"id": 5}}}
    before = replace(after, envelope=replace(after.envelope, data=data))
    next_project = project_with_paratranz_binding(before, replace(_binding(), project_id=99), expected_revision=1)
    harness.service._active = replace(harness.service.active, project=next_project, persisted_project_revision=2)
    assert apply_binding_undo(harness.service, capture_binding_undo(before, next_project), context).is_success
    assert harness.service.active.project.envelope.data == before.envelope.data


def test_binding_apply_rechecks_cas_after_preflight(monkeypatch):
    harness, context, before, after = _prepared()
    original = harness.service.commit_project_update

    def concurrent(project, expected, ctx):
        competing = project_with_paratranz_binding(after, replace(_binding(), project_id=77), expected_revision=1)
        assert original(competing, 1, ctx).is_success
        return original(project, expected, ctx)

    monkeypatch.setattr(harness.service, "commit_project_update", concurrent)
    result = apply_binding_undo(harness.service, capture_binding_undo(before, after), context)
    assert not result.is_success and result.diagnostics[0].code == "PROJECT_UPDATE_STALE"
    assert harness.service.active.project.envelope.data["remote_bindings"]["paratranz"]["project_id"] == 77
