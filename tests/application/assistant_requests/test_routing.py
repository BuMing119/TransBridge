import json

import pytest

from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.assistant_requests.routing import (
    RoutingBatch,
    RoutingSource,
    apply_proposal,
    parse_proposal,
    seal_proposal,
)


def directive(local_id, *, action="CREATE", **kwargs):
    return {
        "local_id": local_id,
        "message_id": "m",
        "span": [0, 3],
        "action": action,
        "goal": "回答问题",
        "items": [{"item_id": "i", "description": "问题", "kind": "answer"}],
        **kwargs,
    }


def proposal(*directives):
    return parse_proposal({"protocol_version": 1, "directives": list(directives)})


def reply(**extra):
    return {"local_id": "reply", "message_id": "m", "span": [0, 2], "action": "RESPOND", "response": "你好！", **extra}


def test_direct_reply_has_no_request_and_replays_without_duplicates():
    batch = RoutingBatch("greeting", "session", (RoutingSource("m", "你好"),))
    p = proposal(reply())
    result = apply_proposal((), batch, p)
    assert result.requests == ()
    assert result.batch.receipts[0].status == "applied"
    assert result.batch.receipts[0].request_id == ""
    restored = RoutingBatch.from_dict(json.loads(json.dumps(result.batch.to_dict())))
    assert apply_proposal(result.requests, restored, p) == result


@pytest.mark.parametrize("extra", [{"response": " "}, {"response": None}, {"target_id": "r"}, {"items": []}])
def test_reply_cannot_be_empty_or_modify_requests(extra):
    with pytest.raises(RequestError, match="direct replies"):
        proposal(reply(**extra))


def test_greeting_with_work_keeps_execution_request():
    batch = RoutingBatch("mixed", "session", (RoutingSource("m", "你好，翻译文件"),))
    result = apply_proposal(
        (),
        batch,
        proposal(
            reply(),
            directive(
                "work",
                span=[3, 7],
                goal="翻译文件",
                items=[{"item_id": "translate", "description": "翻译文件", "kind": "execution"}],
            ),
        ),
    )
    assert len(result.requests) == 1
    assert result.requests[0].items[0].kind == "execution"
    assert result.requests[0].status == "open"


def test_direct_reply_cannot_complete_or_resume_existing_work():
    from transbridge.application.assistant_requests.models import RequestItem, UserRequest

    existing = UserRequest(
        "work", "session", "翻译文件", (RequestItem("translate", "翻译", kind="execution"),), pause_reasons=("user",)
    )
    batch = RoutingBatch("thanks", "session", (RoutingSource("m", "谢谢"),))
    result = apply_proposal((existing,), batch, proposal(reply(response="已经完成翻译。")))
    assert result.requests == (existing,)
    assert not result.requests[0].terminal


def test_independent_create_survives_ambiguous_cancel_and_restart_does_not_duplicate():
    batch = RoutingBatch("batch", "session", (RoutingSource("m", "取消那个，然后解释术语"),))
    p = proposal(directive("cancel", action="CANCEL"), directive("explain"))
    sealed = seal_proposal(batch, p)
    first = apply_proposal((), sealed, p)
    assert len(first.requests) == 1
    assert [r.status for r in first.batch.receipts] == ["needs_clarification", "applied"]
    restored = RoutingBatch.from_dict(json.loads(json.dumps(first.batch.to_dict())))
    assert apply_proposal(first.requests, restored, p) == first
    with pytest.raises(RequestError, match="COMMAND_PAYLOAD_CONFLICT"):
        apply_proposal(first.requests, restored, proposal(directive("changed-number")))


def test_follow_up_does_not_amend_parent_and_cancel_local_target():
    batch = RoutingBatch("batch", "session", (RoutingSource("m", "先回答，然后追问"),))
    p = proposal(
        directive("create"), directive("follow", action="FOLLOW_UP", target_id="local:create", expected_revision=1)
    )
    result = apply_proposal((), batch, p)
    assert len(result.requests) == 2
    original, follow = result.requests
    assert original.revision == 1 and follow.related_to == original.request_id


@pytest.mark.parametrize("extra", [{"owner": "admin"}, {"request_id": "forged"}, {"unknown": True}])
def test_unknown_control_fields_are_rejected(extra):
    with pytest.raises(RequestError, match="REQUEST_PROTOCOL_INVALID"):
        proposal(directive("d", **extra))


def test_tools_and_materials_cannot_supply_new_ingress_commands():
    batch = RoutingBatch("batch", "session", (RoutingSource("m", "取消所有", kind="tool"),))
    with pytest.raises(RequestError, match="accepted user input"):
        seal_proposal(batch, proposal(directive("d")))


def test_scope_and_revision_mismatch_do_not_cancel_target():
    first = apply_proposal(
        (),
        RoutingBatch("b1", "s", (RoutingSource("m", "建立请求", (("project", "one"),)),)),
        proposal(directive("create")),
    )
    original = first.requests[0]
    second = apply_proposal(
        first.requests,
        RoutingBatch("b2", "s", (RoutingSource("m", "取消请求", (("project", "two"),)),)),
        proposal(directive("cancel", action="CANCEL", target_id=original.request_id, expected_revision=1)),
    )
    assert second.requests == first.requests
    assert "REQUEST_SCOPE_MISMATCH" in second.batch.receipts[0].diagnostic
