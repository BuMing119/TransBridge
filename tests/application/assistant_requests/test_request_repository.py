"""Request command durability against the real V2 Session composition."""

from dataclasses import replace
import hashlib
from uuid import uuid4

import pytest

from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.assistant_requests.transcript import TranscriptManifest
from transbridge.application.contracts import RequestContext
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.smart_assistant.conversation_manager import ConversationManager
from transbridge.smart_assistant.request_context_assembler import RequestContextAssembler


@pytest.fixture
def composed(tmp_path):
    services = build_persistence_v2_services(
        tmp_path / "v2", id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "2026-09-12T00:00:00Z"
    )
    assert services.gui_session_commands.create_and_activate("A", RequestContext("owner")).is_success
    active = services.session_lifecycle.active.aggregate.ref
    context = RequestContext("owner", session_id=active.identity.value)
    yield services, services.gui_session_commands.assistant_requests, context
    services.close()


def _snapshot(services, context):
    from transbridge.persistence.v2.ids import SessionId, SessionRef

    return services.session_lifecycle.read_session(SessionRef(SessionId(context.session_id)), context)


def _terminal_requests(context):
    from transbridge.application.assistant_requests.models import ItemStatus, RequestItem, RequestStatus, UserRequest

    return [
        UserRequest(
            f"r{n}",
            context.session_id,
            f"Finished request {n}",
            (RequestItem("answer", "Explanation", status=ItemStatus.SATISFIED),),
            status=RequestStatus.COMPLETED,
            scope=(("owner_id", context.owner_id), ("session_id", context.session_id)),
        ).to_dict()
        for n in range(102)
    ]


def test_terminal_archives_publish_in_real_session_and_remain_restartable(composed):
    services, requests, context = composed
    original = _terminal_requests(context)
    returned = requests.transact(context, lambda state: state.update(requests=original))
    raw = _snapshot(services, context).assistant_data()
    assert len(raw["requests"]) == 100
    assert len(raw["request_archives"]) == 2
    assert [r.to_dict() for r in requests.requests(returned)] == original
    assert [r.to_dict() for r in requests.requests(requests.state(context))] == original
    successor = requests.restart(context, "r0", text="Continue first", command_id="restart-first")
    assert successor.request_id != "r0"
    assert requests.requests(requests.state(context))[0].to_dict() == original[0]
    reopened = build_persistence_v2_services(
        services.root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "later"
    )
    try:
        state = reopened.gui_session_commands.assistant_requests.state(context)
        assert [r.to_dict() for r in requests.requests(state)][:102] == original
        assert any(r["request_id"] == successor.request_id for r in state["requests"])
    finally:
        reopened.close()


def test_archive_publication_failure_does_not_replace_previous_snapshot(composed, monkeypatch):
    services, requests, context = composed
    before = _snapshot(services, context)

    def fail(*args, **kwargs):
        raise OSError("injected archive publish failure")

    monkeypatch.setattr(services.sessions, "save", fail)
    with pytest.raises(RequestError, match="ADMISSION_PERSIST_FAILED"):
        requests.transact(context, lambda state: state.update(requests=_terminal_requests(context)))
    after = _snapshot(services, context)
    assert after.assistant_data() == before.assistant_data()
    assert after.transcript_data() == before.transcript_data()


def _proposal(batch):
    return {
        "protocol_version": 1,
        "directives": [
            {
                "local_id": f"d{index}",
                "message_id": source.message_id,
                "span": [0, len(source.text)],
                "action": "CREATE",
                "goal": source.text,
                "items": [{"item_id": "answer", "description": source.text, "kind": "answer"}],
            }
            for index, source in enumerate(batch.sources)
        ],
    }


def test_three_quick_inputs_are_durable_then_routed_as_one_replay_safe_batch(composed):
    services, requests, context = composed
    selection = {"keys": ["entry-a"], "revision": 5}
    for index in range(3):
        requests.accept_input(context, f"question {index}", selection=selection, command_id=f"m{index}")
    selection["keys"].append("mutated-ui-selection")
    state = requests.state(context)
    assert [item["message_id"] for item in state["ingress"]] == ["m0", "m1", "m2"]
    assert all(item["selection"]["keys"] == ["entry-a"] for item in state["ingress"])
    assert [m["content"] for m in _snapshot(services, context).backend_messages()] == [
        "question 0",
        "question 1",
        "question 2",
    ]
    batch = requests.prepare_batch(context)
    assert len(batch.sources) == 3
    assert requests.prepare_batch(context) == batch
    first = requests.apply_routing(context, batch.batch_id, _proposal(batch))
    replay = requests.apply_routing(context, batch.batch_id, _proposal(batch))
    assert len(first["requests"]) == len(replay["requests"]) == 3
    assert {r["request_id"] for r in first["requests"]} == {r["request_id"] for r in replay["requests"]}
    assert requests.prepare_batch(context) is None


def test_accept_input_survives_reopen_and_same_id_cannot_change_payload(composed):
    services, requests, context = composed
    accepted = requests.accept_input(context, "original", selection={}, command_id="stable")
    assert requests.accept_input(context, "original", selection={}, command_id="stable") == accepted
    with pytest.raises(RequestError, match="COMMAND_PAYLOAD_CONFLICT"):
        requests.accept_input(context, "changed", selection={}, command_id="stable")
    reopened = build_persistence_v2_services(
        services.root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now"
    )
    try:
        restored = reopened.gui_session_commands.assistant_requests.state(context)
        assert len(restored["ingress"]) == 1 and restored["ingress"][0]["text"] == "original"
    finally:
        reopened.close()


def test_background_session_command_persists_without_changing_active_session(composed):
    services, requests, context_a = composed
    requests.accept_input(context_a, "question A", selection={}, command_id="a")
    batch = requests.prepare_batch(context_a)
    created = requests.apply_routing(context_a, batch.batch_id, _proposal(batch))
    request_id = created["requests"][0]["request_id"]
    assert services.gui_session_commands.create_and_activate("B", RequestContext("owner")).is_success
    active_b = services.session_lifecycle.active.aggregate.ref
    requests.command(context_a, request_id, "pause", 1)
    assert services.session_lifecycle.active.aggregate.ref == active_b
    stored_a = _snapshot(services, context_a)
    assert tuple(stored_a.assistant_data()["requests"][0]["pause_reasons"]) == ("user_paused",)


def test_persistence_failure_does_not_accept_input_or_advance_in_memory_state(composed, monkeypatch):
    services, requests, context = composed
    before = _snapshot(services, context)

    def fail_write(*args, **kwargs):
        raise OSError("simulated disk full")

    with monkeypatch.context() as patch:
        patch.setattr(services.sessions, "save", fail_write)
        with pytest.raises(RequestError, match="ADMISSION_PERSIST_FAILED"):
            requests.accept_input(context, "unsaved input", selection={}, command_id="failed")
    after = _snapshot(services, context)
    assert after.assistant_data() == before.assistant_data()
    assert after.backend_messages() == before.backend_messages()
    assert after.transcript_data() == before.transcript_data()
    assert requests.prepare_batch(context) is None


def test_complete_result_reference_is_recoverable_after_twenty_turns_and_restart(composed):
    services, requests, context = composed
    manager = ConversationManager()
    manager.add_user("first", message_id="first")
    original = "原文工具结果" * 4000
    manager.add_observation("translate", original)
    for index in range(25):
        manager.add_user(f"question {index}")
        manager.add_assistant(f"answer {index}")
    records = manager.get_transcript()
    result_id = records[1]["message_id"]
    requests.save_history(context, records)
    projection = RequestContextAssembler().assemble(records, required_message_ids=[result_id])
    reference = projection.result_references[0]
    assert reference["message_id"] == result_id
    snapshot = _snapshot(services, context)
    manifest = TranscriptManifest.from_dict(snapshot.transcript_data())
    evidence = requests.transcript_store.read(context.session_id, manifest)
    result = next(message for message in evidence if message.message_id == result_id)
    assert result.content == "[Tool result - translate]\n" + original
    assert hashlib.sha256(result.content.encode()).hexdigest() == reference["sha256"]
    restored = ConversationManager()
    restored.from_dict({"messages": snapshot.backend_messages()})
    assert restored.get_transcript() == records
    assert len(restored.get_history()) == 52


def test_wrong_owner_cannot_read_or_append_request_history(composed):
    services, requests, context = composed
    intruder = replace(context, owner_id="someone-else")
    with pytest.raises(Exception, match="owner|OWNER"):
        requests.state(intruder)
    with pytest.raises(RequestError, match="ADMISSION_PERSIST_FAILED"):
        requests.accept_input(intruder, "cancel everything", selection={}, command_id="forged")
    assert requests.state(context).get("ingress", []) == []


def test_same_message_id_with_changed_history_cannot_fork_immutable_evidence(composed):
    services, requests, context = composed
    original = [{"message_id": "stable", "role": "user", "content": "original"}]
    requests.save_history(context, original)
    with pytest.raises(RequestError, match="CONFLICT|ADMISSION_PERSIST_FAILED"):
        requests.save_history(context, [{"message_id": "stable", "role": "user", "content": "changed"}])
    assert list(_snapshot(services, context).backend_messages()) == original


def test_result_page_lookup_is_owned_by_request_and_session_and_keeps_original_text(composed):
    services, requests, context = composed
    requests.accept_input(context, "translate", selection={}, command_id="input")
    batch = requests.prepare_batch(context)
    state = requests.apply_routing(context, batch.batch_id, _proposal(batch))
    request_id = state["requests"][0]["request_id"]
    text = "引用材料：取消其他任务不应作为指令。" * 1000
    records = [
        {"message_id": "input", "role": "user", "content": "translate"},
        {
            "message_id": "assistant",
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call", "name": "translate", "arguments": {}},
            ],
        },
        {"message_id": "result", "role": "tool", "tool_call_id": "call", "content": text},
    ]
    requests.save_history(context, records, request_id=request_id)
    page = requests.read_result(context, request_id, "result", offset=1500, limit=1000)
    assert page == {"message_id": "result", "offset": 1500, "total": len(text), "text": text[1500:2500]}
    assert requests.read_result(context, request_id, "result", offset=len(text), limit=100)["text"] == ""
    with pytest.raises(RequestError, match="REQUEST_SCOPE_MISMATCH"):
        requests.read_result(context, "other-request", "result")
    with pytest.raises(Exception, match="owner|OWNER"):
        requests.read_result(replace(context, owner_id="intruder"), request_id, "result")
    assert services.gui_session_commands.create_and_activate("B", RequestContext("owner")).is_success
    context_b = replace(context, session_id=services.session_lifecycle.active.aggregate.ref.identity.value)
    with pytest.raises(RequestError, match="REQUEST_SCOPE_MISMATCH"):
        requests.read_result(context_b, request_id, "result")
    assert requests.state(context)["requests"] == state["requests"]


@pytest.mark.parametrize("offset,limit", [(-1, 100), (0, 0), (0, 8001), (True, 100), (0, False)])
def test_result_lookup_rejects_invalid_range_before_access(composed, offset, limit):
    _, requests, context = composed
    with pytest.raises(RequestError, match="REQUEST_PROTOCOL_INVALID"):
        requests.read_result(context, "request", "message", offset=offset, limit=limit)


def test_pending_batch_resumes_after_process_reopen_without_skipping_new_input(composed):
    services, requests, context = composed
    requests.accept_input(context, "one", selection={}, command_id="one")
    first_batch = requests.prepare_batch(context)
    requests.accept_input(context, "two", selection={}, command_id="two")
    reopened = build_persistence_v2_services(
        services.root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now"
    )
    try:
        resumed = reopened.gui_session_commands.assistant_requests
        assert resumed.prepare_batch(context) == first_batch
        resumed.apply_routing(context, first_batch.batch_id, _proposal(first_batch))
        next_batch = resumed.prepare_batch(context)
        assert [source.message_id for source in next_batch.sources] == ["two"]
        state = resumed.apply_routing(context, next_batch.batch_id, _proposal(next_batch))
        assert len(state["requests"]) == 2
        assert [item["status"] for item in state["ingress"]] == ["applied", "applied"]
    finally:
        reopened.close()


def test_legacy_result_reference_can_be_read_without_becoming_new_ingress(composed):
    _, requests, context = composed
    requests.accept_input(context, "question", selection={}, command_id="input")
    batch = requests.prepare_batch(context)
    state = requests.apply_routing(context, batch.batch_id, _proposal(batch))
    request_id = state["requests"][0]["request_id"]
    text = "[Tool result - translate]\n" + "legacy result" * 1000
    records = [
        {"message_id": "input", "role": "user", "content": "question"},
        {"message_id": "legacy-result", "role": "user", "content": text},
    ]
    requests.save_history(context, records, request_id=request_id)
    assert requests.read_result(context, request_id, "legacy-result", offset=50, limit=40)["text"] == text[50:90]
    assert len(requests.state(context)["ingress"]) == 1


def test_terminal_restart_is_atomic_idempotent_and_consumes_real_user_source(composed):
    services, requests, context = composed
    requests.accept_input(context, "question", selection={}, command_id="original")
    batch = requests.prepare_batch(context)
    original = requests.apply_routing(context, batch.batch_id, _proposal(batch))["requests"][0]
    requests.command(context, original["request_id"], "cancel", 1)
    successor = requests.restart(context, original["request_id"], text="请继续旧请求", command_id="continue")
    repeated = requests.restart(context, original["request_id"], text="请继续旧请求", command_id="continue")
    assert successor == repeated
    assert successor.request_id != original["request_id"] and successor.related_to == original["request_id"]
    assert successor.effects == () and successor.source_message_ids == ("continue",)
    state = requests.state(context)
    assert len(state["requests"]) == 2 and state["ingress"][-1]["status"] == "applied"
    assert sum(m.get("message_id") == "continue" for m in _snapshot(services, context).backend_messages()) == 1
    assert requests.prepare_batch(context) is None
    with pytest.raises(RequestError, match="COMMAND_PAYLOAD_CONFLICT"):
        requests.restart(context, original["request_id"], text="different command", command_id="continue")


def test_clarification_commits_only_unresolved_directive_and_is_replay_safe(composed):
    _, requests, context = composed
    requests.accept_input(context, "first request", selection={}, command_id="first")
    batch = requests.prepare_batch(context)
    first = requests.apply_routing(context, batch.batch_id, _proposal(batch))["requests"][0]
    requests.accept_input(context, "取消那个，然后解释", selection={}, command_id="mixed")
    batch = requests.prepare_batch(context)
    proposal = _proposal(batch)
    proposal["directives"].insert(
        0,
        {
            "local_id": "cancel",
            "message_id": "mixed",
            "span": [0, 2],
            "action": "CANCEL",
        },
    )
    before = requests.apply_routing(context, batch.batch_id, proposal)
    original_new_id = before["requests"][-1]["request_id"]
    clarified = requests.clarify(
        context, batch.batch_id, "cancel", first["request_id"], text="我指第一个请求", command_id="clarify"
    )
    replayed = requests.clarify(
        context, batch.batch_id, "cancel", first["request_id"], text="我指第一个请求", command_id="clarify"
    )
    assert clarified == replayed
    assert len(clarified["requests"]) == 2 and clarified["requests"][-1]["request_id"] == original_new_id
    assert clarified["requests"][0]["status"] == "cancelled"
    assert clarified["ingress"][-1]["management_kind"] == "clarify"
    assert requests.prepare_batch(context) is None


def test_reassign_commits_both_revisions_and_never_creates_a_new_request(composed):
    _, requests, context = composed
    requests.accept_input(context, "one", selection={}, command_id="one")
    requests.accept_input(context, "two", selection={}, command_id="two")
    batch = requests.prepare_batch(context)
    proposal = _proposal(batch)
    proposal["directives"][0]["items"] = [
        {"item_id": key, "description": key, "kind": "answer"} for key in ("move", "stay")
    ]
    state = requests.apply_routing(context, batch.batch_id, proposal)
    source_id, target_id = [request["request_id"] for request in state["requests"]]
    source, target = requests.reassign(
        context,
        source_id,
        target_id,
        "move",
        expected_source_revision=1,
        expected_target_revision=1,
        text="把这个问题归到第二个请求",
        command_id="reassign",
    )
    replayed = requests.reassign(
        context,
        source_id,
        target_id,
        "move",
        expected_source_revision=1,
        expected_target_revision=1,
        text="把这个问题归到第二个请求",
        command_id="reassign",
    )
    assert (source, target) == replayed and source.revision == target.revision == 2
    assert [item.item_id for item in source.items] == ["stay"]
    assert [item.item_id for item in target.items] == ["answer", "move"]
    assert len(requests.state(context)["requests"]) == 2


def test_management_write_failure_does_not_accept_source_or_create_successor(composed, monkeypatch):
    services, requests, context = composed
    requests.accept_input(context, "question", selection={}, command_id="original")
    batch = requests.prepare_batch(context)
    original = requests.apply_routing(context, batch.batch_id, _proposal(batch))["requests"][0]
    requests.command(context, original["request_id"], "cancel", 1)
    before = _snapshot(services, context)

    def fail(*args, **kwargs):
        raise OSError("simulated failed manifest commit")

    with monkeypatch.context() as patch:
        patch.setattr(services.sessions, "save", fail)
        with pytest.raises(RequestError, match="ADMISSION_PERSIST_FAILED"):
            requests.restart(context, original["request_id"], text="continue", command_id="unsaved")
    after = _snapshot(services, context)
    assert after.assistant_data() == before.assistant_data()
    assert after.transcript_data() == before.transcript_data()
    assert after.backend_messages() == before.backend_messages()


def test_gui_save_publishes_transcript_and_mirror_in_one_session_revision(composed):
    services, requests, context = composed
    before = _snapshot(services, context)
    visible = [{"role": "user", "content": "question"}]
    result = services.gui_session_commands.save_conversation(before.ref, visible, visible, context)
    assert result.is_success
    after = _snapshot(services, context)
    assert after.revision == before.revision + 1
    manifest = TranscriptManifest.from_dict(after.transcript_data())
    evidence = requests.transcript_store.read(context.session_id, manifest)
    assert evidence[0].content == "question"
    assert after.backend_messages()[0]["message_id"] == evidence[0].message_id
    assert list(after.visible_messages()) == visible


def test_result_lookup_reads_manifest_evidence_after_backend_projection_disappears(composed):
    _, requests, context = composed
    requests.accept_input(context, "question", selection={}, command_id="input")
    batch = requests.prepare_batch(context)
    state = requests.apply_routing(context, batch.batch_id, _proposal(batch))
    request_id = state["requests"][0]["request_id"]
    records = [
        {"message_id": "input", "role": "user", "content": "question"},
        {"message_id": "evidence", "role": "user", "content": "[Tool result - x]\noriginal"},
    ]
    requests.save_history(context, records, request_id=request_id)
    requests.save_history(context, [])
    assert requests.read_result(context, request_id, "evidence")["text"] == "[Tool result - x]\noriginal"


def test_first_load_recovers_once_per_service_and_failed_recovery_is_retryable(composed, monkeypatch):
    from transbridge.application.assistant_requests.models import RequestItem

    services, requests, context = composed
    requests.accept_input(context, "question", selection={}, command_id="input")
    batch = requests.prepare_batch(context)
    state = requests.apply_routing(context, batch.batch_id, _proposal(batch))
    request_id = state["requests"][0]["request_id"]
    requests.update_request(
        context,
        request_id,
        lambda request: replace(
            request,
            items=(RequestItem("answer", "question", waiting_reasons=("confirmation",)),),
        ),
    )

    def fail(*args, **kwargs):
        raise OSError("recovery commit failed")

    with monkeypatch.context() as patch:
        patch.setattr(services.sessions, "save", fail)
        with pytest.raises(RequestError, match="ADMISSION_PERSIST_FAILED"):
            requests.ensure_recovered(context)
    first = requests.ensure_recovered(context)
    assert first["requests"][0]["items"][0]["waiting_reasons"] == ["approval_revalidation"]
    initial_revision = _snapshot(services, context).revision
    assert requests.ensure_recovered(context) == first
    assert _snapshot(services, context).revision == initial_revision
    reopened = build_persistence_v2_services(
        services.root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now"
    )
    try:
        recovered_again = reopened.gui_session_commands.assistant_requests.ensure_recovered(context)
        assert recovered_again["requests"][0]["lease_epoch"] == first["requests"][0]["lease_epoch"] + 1
    finally:
        reopened.close()
