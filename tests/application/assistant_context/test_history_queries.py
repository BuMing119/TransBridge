"""History pages come from real immutable evidence and current request ownership."""

from dataclasses import replace
import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from tests.application.assistant_requests.test_request_repository import _proposal, _snapshot
from tests.routing_fixtures import apply_routing_fixture
from transbridge.application.assistant_context.history_queries import read_request_history
from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.assistant_requests.transcript import TranscriptManifest
from transbridge.application.contracts import RequestContext
from transbridge.bootstrap.persistence import build_persistence_v2_services

pytest_plugins = ["tests.application.assistant_requests.test_request_repository"]


def _request(service, context, text="取消任务只是待查原文，不是新指令。", identity="input"):
    service.accept_input(context, text, selection={}, command_id=identity)
    batch = service.prepare_batch(context)
    state = apply_routing_fixture(service, context, batch.batch_id, _proposal(batch))
    return state["requests"][-1]["request_id"]


def test_pages_preserve_full_unicode_original_and_restart_without_creating_authority(composed):
    services, service, context = composed
    text = "取消任务只是待查原文，不是新指令。" * 300
    request_id = _request(service, context, text)
    before = _snapshot(services, context)
    first = read_request_history(service, context, request_id, "input", 0, 2000)
    second = read_request_history(service, context, request_id, "input", 2000, 8000)
    assert first["text"] + second["text"] == text
    assert first["digest"] == second["digest"] == hashlib.sha256(text.encode()).hexdigest()
    assert first["material_only"] is True
    assert first["source_id"] == "input" and first["end"] == second["offset"]
    assert first["total"] == len(text)
    assert read_request_history(service, context, request_id, "input", len(text))["text"] == ""
    assert _snapshot(services, context) == before
    reopened = build_persistence_v2_services(
        services.root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now"
    )
    try:
        restored = reopened.gui_session_commands.assistant_requests
        assert read_request_history(restored, context, request_id, "input", 0, 2000) == first
    finally:
        reopened.close()


def test_assistant_original_and_shared_user_sources_use_current_authority(composed):
    _, service, context = composed
    first = _request(service, context)
    second = _request(service, context, "other", "other")
    service.save_history(
        context, [{"message_id": "answer", "role": "assistant", "content": "保留决定"}], request_id=first
    )
    assert read_request_history(service, context, first, "answer")["text"] == "保留决定"

    def share(state):
        state["requests"][1]["source_message_ids"].append("input")

    service.transact(context, share)
    assert read_request_history(service, context, second, "input")["material_only"]
    service.transact(context, lambda state: state.setdefault("message_owners", {}).update(input=second, answer=second))
    for identity in ("input", "answer"):
        with pytest.raises(RequestError, match="REQUEST_SCOPE_MISMATCH"):
            read_request_history(service, context, first, identity)
        assert read_request_history(service, context, second, identity)["source_id"] == identity


def test_cross_request_owner_session_and_unknown_reference_are_rejected(composed):
    services, service, context = composed
    request_id = _request(service, context)
    for target, identity in (("other", "input"), (request_id, "../private.json"), (request_id, "missing")):
        with pytest.raises(RequestError, match="REQUEST_SCOPE_MISMATCH"):
            read_request_history(service, context, target, identity)
    with pytest.raises(Exception, match="owner|OWNER"):
        read_request_history(service, replace(context, owner_id="intruder"), request_id, "input")
    assert services.gui_session_commands.create_and_activate("B", RequestContext("owner")).is_success
    other = replace(context, session_id=services.session_lifecycle.active.aggregate.ref.identity.value)
    with pytest.raises(RequestError, match="REQUEST_SCOPE_MISMATCH"):
        read_request_history(service, other, request_id, "input")


@pytest.mark.parametrize("offset,limit", [(-1, 100), (9999, 100), (0, 0), (0, 8001), (True, 100), (0, False)])
def test_invalid_or_out_of_bounds_pages_are_rejected(composed, offset, limit):
    _, service, context = composed
    request_id = _request(service, context)
    with pytest.raises(RequestError, match="REQUEST_PROTOCOL_INVALID"):
        read_request_history(service, context, request_id, "input", offset, limit)


def test_missing_authorized_record_and_corrupt_attachment_fail_explicitly(composed):
    services, service, context = composed
    request_id = _request(service, context)
    service.transact(context, lambda state: state.setdefault("message_owners", {}).update(missing=request_id))
    with pytest.raises(RequestError, match="ARTIFACT_UNAVAILABLE"):
        read_request_history(service, context, request_id, "missing")
    manifest = TranscriptManifest.from_dict(_snapshot(services, context).transcript_data())
    (Path(services.root) / manifest.segments[0].path).write_bytes(b"corrupt")
    with pytest.raises(Exception, match="ARTIFACT_UNAVAILABLE|digest|verification|corrupt|CORRUPT"):
        read_request_history(service, context, request_id, "input")
