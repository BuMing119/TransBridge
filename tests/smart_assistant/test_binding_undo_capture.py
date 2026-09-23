"""The actual switch tool records only its local binding update."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.smart_assistant.test_undo_capture import _adapter as _base_adapter
from transbridge.application.assistant_requests.models import EffectStatus, RequestError, RequestStatus
from transbridge.application.projects.remote_binding import (
    ParaTranzProjectBinding,
    ProjectRemoteBindingService,
    project_with_paratranz_binding,
)
from transbridge.smart_assistant.tools import tool_paratranz
from transbridge.smart_assistant.tools.binding_undo_capture import capture_binding_command

pytest_plugins = ["tests.application.assistant_requests.test_request_repository"]


def _adapter(composed):
    case, context = _base_adapter(composed)
    _, requests, request_context, _, _, _, execution, _ = case
    requests.update_request(
        request_context,
        execution.request_id,
        lambda request: replace(
            request, status=RequestStatus.OPEN, effects=(replace(request.effects[0], status=EffectStatus.RUNNING),)
        ),
    )
    return case, context


def _settle(case):
    _, requests, request_context, _, _, _, execution, _ = case
    requests.update_request(
        request_context,
        execution.request_id,
        lambda request: replace(
            request,
            status=RequestStatus.CANCELLED,
            effects=(replace(request.effects[0], status=EffectStatus.SUCCEEDED),),
        ),
    )


def test_switch_tool_captures_local_binding_and_inverse_does_not_call_remote(composed, monkeypatch):
    case, context = _adapter(composed)
    _, requests, request_context, harness, _, _, execution, undo = case
    bindings = ProjectRemoteBindingService(harness.service)
    app = context.app_context
    app.active_project_id = request_context.project_id
    app.set_paratranz_binding = lambda binding: bindings.set_paratranz_binding(
        binding, request_context, expected_project_revision=app.project_revision
    )
    reads = []

    def get_project(project_id, **_kwargs):
        reads.append(project_id)
        return {"id": project_id, "name": "Target", "visibility": "private"}

    monkeypatch.setattr(
        tool_paratranz, "_get_paratranz_client", lambda *_: (SimpleNamespace(get_project=get_project), None, None)
    )
    result = tool_paratranz._tool_switch_paratranz_project({"project_id": 42}, context)
    assert result.success and reads == [42]
    record = requests.state(request_context)["undo_rounds"][execution.work_round_id]
    assert len(record["commits"]) == 1
    commit = record["commits"][0]
    assert commit["kind"] == "binding" and commit["status"] == "recorded"
    _settle(case)
    assert undo.preview(request_context, execution.work_round_id)["available"]
    assert not undo.undo(request_context, execution.work_round_id)["partial"]
    assert reads == [42]
    assert "paratranz" not in harness.service.active.project.envelope.data.get("remote_bindings", {})


@pytest.mark.parametrize("concurrent", [False, True])
def test_capture_does_not_attribute_other_project_fields_or_concurrent_commit(composed, concurrent):
    case, context = _adapter(composed)
    _, requests, request_context, harness, _, _, execution, _ = case
    before = harness.service.active.project
    next_project = project_with_paratranz_binding(
        before,
        ParaTranzProjectBinding(42, "Target", "https://paratranz.cn"),
        expected_revision=before.envelope.revision,
    )

    def mutation():
        project = next_project
        if not concurrent:
            project = replace(
                project, envelope=replace(project.envelope, data={**project.envelope.data, "name": "also changed"})
            )
        result = harness.service.commit_project_update(project, before.envelope.revision, request_context)
        assert result.is_success
        if concurrent:
            intervening = replace(
                project,
                envelope=replace(
                    project.envelope,
                    revision=project.envelope.revision + 1,
                    data={**project.envelope.data, "name": "other writer"},
                ),
            )
            assert harness.service.commit_project_update(
                intervening, project.envelope.revision, request_context
            ).is_success
        return result

    assert capture_binding_command(context, mutation).is_success
    record = requests.state(request_context)["undo_rounds"][execution.work_round_id]["commits"][0]
    assert record["status"] == "unresolved" and "receipt" not in record


def test_cancelled_binding_effect_cannot_write_or_record_intent(composed):
    case, context = _adapter(composed)
    _, requests, request_context, harness, _, _, execution, _ = case
    before = harness.service.active.project
    _settle(case)
    mutations = []
    with pytest.raises(RequestError):
        capture_binding_command(context, lambda: mutations.append("unexpected write"))
    assert mutations == []
    assert harness.service.active.project == before
    assert requests.state(request_context)["undo_rounds"][execution.work_round_id]["commits"] == []
