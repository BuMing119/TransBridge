"""Stored v1 summaries remain readable without any new legacy summary writes."""

from dataclasses import replace
from uuid import uuid4

import pytest

from transbridge.application.assistant_requests.models import RequestItem, UserRequest
from transbridge.application.assistant_requests.summaries import _reconstruct_legacy_summary
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


def _seed_legacy_summary(service, context, request):
    summaries = RequestSummaryService(service)
    history, _, _ = summaries.prepared_material(context, request.request_id)
    summary = _reconstruct_legacy_summary(request, history)
    assert summary is not None
    service.transact(context, lambda state: state.update(request_summaries={request.request_id: summary.to_dict()}))
    return summary


def test_legacy_summary_reopens_and_preparation_never_writes(summary_session, monkeypatch):
    services, service, context, request, _ = summary_session
    summary = _seed_legacy_summary(service, context, request)
    snapshot = services.session_lifecycle.read_session(SessionRef(SessionId(context.session_id)), context)
    reopened = build_persistence_v2_services(
        services.root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "later"
    )
    try:

        def forbid_write(*args, **kwargs):
            raise AssertionError("Legacy summary reader must not persist refreshed material")

        monkeypatch.setattr(reopened.sessions, "save", forbid_write)
        restored = RequestSummaryService(reopened.gui_session_commands.assistant_requests)
        history, material, _ = restored.prepared_material(context, request.request_id)
        assert material == summary
        assert len(history) == 30
        assert reopened.session_lifecycle.read_session(snapshot.ref, context).revision == snapshot.revision
    finally:
        reopened.close()


def test_no_summary_is_generated_when_old_cache_is_absent(summary_session):
    _, service, context, request, _ = summary_session
    before = service.state(context)
    history, summary, _ = RequestSummaryService(service).prepared_material(context, request.request_id)
    assert len(history) == 30
    assert summary is None
    assert service.state(context) == before
    assert "request_summaries" not in before


def test_stale_summary_is_not_refreshed_or_deleted(summary_session):
    _, service, context, request, _ = summary_session
    summary = _seed_legacy_summary(service, context, request)
    service.update_request(context, request.request_id, lambda current: replace(current, revision=2, goal="New goal"))
    before = service.state(context)
    _, material, _ = RequestSummaryService(service).prepared_material(context, request.request_id)
    assert material is None
    assert service.state(context) == before
    assert before["request_summaries"][request.request_id] == summary.to_dict()


def test_malformed_or_altered_cache_is_ignored_without_repair(summary_session):
    _, service, context, request, _ = summary_session
    summary = _seed_legacy_summary(service, context, request)
    summaries = RequestSummaryService(service)
    scoped, _, _ = summaries.prepared_material(context, request.request_id)
    for raw in ({"schema_version": 999}, {**summary.to_dict(), "text": '{"material_only":false}'}):
        state = {"request_summaries": {request.request_id: raw}}
        assert summaries.read_valid(state, request, scoped) is None
        assert state["request_summaries"][request.request_id] == raw
