"""Derived summaries are actually persisted and fenced against concurrent request changes."""

from dataclasses import replace
from uuid import uuid4

import pytest

from transbridge.application.assistant_requests.models import RequestError, RequestItem, UserRequest
from transbridge.application.assistant_requests.summary_service import RequestSummaryService
from transbridge.application.contracts import RequestContext
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.v2.ids import SessionId, SessionRef


@pytest.fixture
def summary_session(tmp_path):
    services = build_persistence_v2_services(
        tmp_path / "data", id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now"
    )
    assert services.gui_session_commands.create_and_activate("Summary", RequestContext("owner")).is_success
    context = RequestContext("owner", session_id=services.session_lifecycle.active.aggregate.ref.identity.value)
    service = services.gui_session_commands.assistant_requests
    request = UserRequest("request", context.session_id, "Explain decisions", (RequestItem("i", "explain"),))
    records = [
        {"message_id": f"m{n}", "role": "assistant", "content": f"Decision {n}: preserve this fact. " + "detail " * 90}
        for n in range(30)
    ]
    service.transact(context, lambda state: state.update(requests=[request.to_dict()]))
    service.save_history(context, records, request_id=request.request_id)
    yield services, service, context, request, records
    services.close()


def test_summary_persists_reopens_and_unchanged_refresh_does_not_write(summary_session):
    services, service, context, request, _ = summary_session
    summaries = RequestSummaryService(service)
    summary = summaries.refresh(context, request.request_id)
    assert summary is not None
    snapshot = services.session_lifecycle.read_session(SessionRef(SessionId(context.session_id)), context)
    assert snapshot.assistant_data()["request_summaries"][request.request_id] == summary.to_dict()
    assert summaries.refresh(context, request.request_id) == summary
    assert services.session_lifecycle.read_session(snapshot.ref, context).revision == snapshot.revision
    reopened = build_persistence_v2_services(
        services.root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "later"
    )
    try:
        restored = RequestSummaryService(reopened.gui_session_commands.assistant_requests)
        assert restored.refresh(context, request.request_id) == summary
    finally:
        reopened.close()


def test_summary_does_not_publish_if_revision_changes_during_generation(summary_session, monkeypatch):
    from transbridge.application.assistant_requests import summary_service as module

    _, service, context, request, _ = summary_session
    original = module.plan_summary

    def generate(*args):
        summary = original(*args)
        service.update_request(
            context, request.request_id, lambda current: replace(current, revision=2, goal="New goal")
        )
        return summary

    monkeypatch.setattr(module, "plan_summary", generate)
    assert RequestSummaryService(service).refresh(context, request.request_id) is None
    state = service.state(context)
    assert not state.get("request_summaries")
    assert service.requests(state)[0].goal == "New goal"


def test_summary_write_failure_preserves_authoritative_state(summary_session, monkeypatch):
    services, service, context, request, records = summary_session
    before = service.state(context)

    def fail(*args, **kwargs):
        raise OSError("injected disk full")

    monkeypatch.setattr(services.sessions, "save", fail)
    with pytest.raises(RequestError, match="SUMMARY_PERSIST_FAILED"):
        RequestSummaryService(service).refresh(context, request.request_id)
    assert service.state(context) == before
    snapshot = services.session_lifecycle.read_session(SessionRef(SessionId(context.session_id)), context)
    assert list(snapshot.backend_messages()) == records


def test_malformed_or_stale_cache_is_ignored(summary_session):
    _, service, context, request, records = summary_session
    summaries = RequestSummaryService(service)
    summary = summaries.refresh(context, request.request_id)
    scoped = [{**record, "request_id": request.request_id} for record in records]
    assert summaries.read_valid(service.state(context), request, scoped) == summary
    assert summaries.read_valid(service.state(context), replace(request, revision=2), scoped) is None
    assert (
        summaries.read_valid({"request_summaries": {request.request_id: {"schema_version": 999}}}, request, scoped)
        is None
    )
