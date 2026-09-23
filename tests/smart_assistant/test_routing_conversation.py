import json

from transbridge.application.assistant_requests.models import ItemStatus, RequestItem, RequestStatus, UserRequest
from transbridge.application.assistant_requests.routing import RoutingBatch, RoutingSource
from transbridge.smart_assistant.request_router import routing_messages


def request(index, *, completed=True, session="s"):
    return UserRequest(
        f"r-{index}",
        session,
        f"翻译文件{index}",
        (RequestItem("i", "翻译", status=ItemStatus.SATISFIED if completed else ItemStatus.PENDING),),
        status=RequestStatus.COMPLETED if completed else RequestStatus.OPEN,
    )


def payload(text, requests=(), history=()):
    batch = RoutingBatch("b", "s", (RoutingSource("current", text),))
    return json.loads(routing_messages(batch, requests, history=history)[-1]["content"])


def test_thanks_only_receives_bounded_terminal_candidates_and_active_work():
    result = payload("谢谢", [*(request(i) for i in range(100)), request("active", completed=False)])
    assert len(result["requests"]) == 4
    assert result["omitted_request_count"] == 97
    terminal = [r for r in result["requests"] if r["status"] == "completed"]
    assert all("items" not in r and "constraints" not in r for r in terminal)
    assert "items" in result["requests"][0]


def test_explicit_old_request_and_chinese_title_are_found_without_cross_session_candidates():
    requests = [request(i) for i in range(100)] + [request("foreign", session="other")]
    result = payload("继续 r-0", requests)
    assert "r-0" in {r["request_id"] for r in result["requests"]}
    assert "r-foreign" not in {r["request_id"] for r in result["requests"]}
    requests[0] = UserRequest("unique", "s", "检查术语冲突", requests[0].items, status=RequestStatus.COMPLETED)
    assert "unique" in {r["request_id"] for r in payload("再检查术语冲突", requests)["requests"]}


def test_recent_chat_includes_direct_answers_without_tool_protocol_or_current_input():
    history = [
        {"message_id": "u", "role": "user", "content": "你好"},
        {"message_id": "reply", "role": "assistant", "content": "你好，有什么可以帮你？"},
        {
            "message_id": "answer",
            "role": "assistant",
            "content": "上一项工作的分析结论",
            "tool_calls": [{"id": "coverage", "name": "report_answer_coverage"}],
        },
        {"message_id": "call", "role": "assistant", "content": "internal", "tool_calls": [{"id": "c"}]},
        {"message_id": "result", "role": "tool", "content": "private result"},
        {"message_id": "current", "role": "user", "content": "谢谢"},
    ]
    result = payload("谢谢", history=history)
    assert [m["message_id"] for m in result["recent_conversation"]] == ["u", "reply", "answer"]
    assert all("tool_calls" not in m for m in result["recent_conversation"])


def test_recent_chat_is_bounded_and_marks_truncation():
    history = [{"message_id": str(i), "role": "assistant", "content": "字" * 4000} for i in range(100)]
    recent = payload("继续", history=history)["recent_conversation"]
    assert sum(len(m["text"]) for m in recent) <= 12000
    assert len(recent) <= 12 and all(m["truncated"] for m in recent)
