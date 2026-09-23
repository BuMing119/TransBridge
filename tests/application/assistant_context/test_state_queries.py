"""Detail queries remain bounded, read only, and fenced to their admitted request."""

from dataclasses import replace
import json

import pytest

from tests.application.assistant_context.test_history_queries import _request
from tests.application.assistant_requests.test_request_repository import _snapshot
from transbridge.application.assistant_context.state_queries import (
    STATE_RETRIEVAL_TOOL,
    read_request_state,
)
from transbridge.application.assistant_requests.models import RequestError

pytest_plugins = ["tests.application.assistant_requests.test_request_repository"]


def _admit(service, context):
    service.scheduler.activate(context.session_id, "view")
    return service.scheduler.select_next_turn(
        context.session_id, "view", service.requests(service.state(context)), allowed_tools=(STATE_RETRIEVAL_TOOL,)
    ).admission


def test_pages_reassemble_full_unicode_items_without_mutation(composed):
    services, service, context = composed
    _request(service, context)
    admission = _admit(service, context)
    before = _snapshot(services, context)
    page = read_request_state(service, context, admission, "items", limit=11)
    text = page["text"]
    while page["next_offset"] is not None:
        page = read_request_state(service, context, admission, "items", page["next_offset"], 11, page["digest"])
        text += page["text"]
    assert json.loads(text) == json.loads(json.dumps(service.requests(service.state(context))[0].to_dict()["items"]))
    assert page["total_chars"] == len(text) and page["material_only"]
    assert _snapshot(services, context) == before


def test_selection_is_limited_to_current_request_sources(composed):
    _, service, context = composed
    _request(service, context)
    _request(service, context, "other", "other")
    service.transact(context, lambda state: state["ingress"][1].update(selection={"private": "unrelated"}))
    admission = _admit(service, context)
    page = read_request_state(service, context, admission, "selection")
    assert json.loads(page["text"]) == [{}]


def test_changed_state_rejects_continuation(composed):
    _, service, context = composed
    _request(service, context)
    admission = _admit(service, context)
    page = read_request_state(service, context, admission, "items", limit=10)
    service.transact(context, lambda state: state["requests"][0]["items"][0].update(description="changed"))
    with pytest.raises(RequestError, match="REQUEST_STATE_CHANGED"):
        read_request_state(service, context, admission, "items", 10, 10, page["digest"])


@pytest.mark.parametrize("section", ["items", "effects", "evidence", "dispatches", "selection"])
def test_stale_or_foreign_admission_cannot_read_any_section(composed, section):
    _, service, context = composed
    _request(service, context)
    admission = _admit(service, context)
    with pytest.raises(RequestError):
        read_request_state(service, context, replace(admission, request_id="other"), section)
    service.scheduler.release(admission)
    with pytest.raises(RequestError, match="TURN_LEASE_STALE"):
        read_request_state(service, context, admission, section)


@pytest.mark.parametrize(
    "args", [("bad", 0, 10, ""), ("items", True, 10, ""), ("items", 0, 8001, ""), ("items", 1, 10, "")]
)
def test_invalid_arguments_fail_explicitly(composed, args):
    _, service, context = composed
    _request(service, context)
    admission = _admit(service, context)
    with pytest.raises(RequestError, match="REQUEST_PROTOCOL_INVALID"):
        read_request_state(service, context, admission, *args)
