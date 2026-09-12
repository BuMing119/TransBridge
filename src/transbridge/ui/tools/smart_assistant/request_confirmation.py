"""New protocol identities for confirmations presented again after interruption."""

from copy import deepcopy
from uuid import uuid4

from transbridge.infra.llm_tool_calling import LlmToolCall, LlmTurn


def renew_confirmation(parsed, conversation):
    renewed = deepcopy(parsed)
    calls = []
    if renewed.get("mode") == "plan":
        renewed["plan_call_id"] = uuid4().hex
        calls.append(
            LlmToolCall(
                renewed["plan_call_id"],
                "propose_plan",
                {
                    "summary": renewed.get("summary", ""),
                    "steps": renewed["steps"],
                },
            )
        )
    else:
        for step in renewed.get("steps", ()):
            step["tool_call_id"] = uuid4().hex
            calls.append(LlmToolCall(step["tool_call_id"], step["tool"], step.get("args", {})))
    conversation.add_assistant_turn(
        LlmTurn(
            text=renewed.get("summary", ""),
            tool_calls=tuple(calls),
            stop_reason="tool_calls",
        )
    )
    return renewed
