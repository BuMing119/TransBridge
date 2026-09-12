"""Commit answer coverage and publish its receipt as one foreground transition."""

import json
from uuid import uuid4

from transbridge.application.assistant_requests.journal import EventCause
from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.assistant_requests.scheduler import commit_answer
from transbridge.smart_assistant.request_protocol import COVERAGE_TOOL


def accept_answer(binding, parsed, turn):
    arguments = parsed["arguments"]
    if set(arguments) != {"item_ids", "dispositions"} or set(arguments["item_ids"]) != set(arguments["dispositions"]):
        raise RequestError("REQUEST_PROTOCOL_INVALID", "answer coverage fields disagree")
    context, admission = binding.context, binding.admission
    history = binding.facade._conversation.get_transcript()
    message_id, receipt_id = history[-1]["message_id"], uuid4().hex
    receipt = {
        "role": "tool",
        "tool_call_id": parsed["tool_call_id"],
        "name": COVERAGE_TOOL,
        "content": json.dumps({"accepted": True}),
        "display_summary": "",
        "is_error": False,
        "message_id": receipt_id,
    }

    def save():
        binding.service.update_request(
            context,
            admission.request_id,
            lambda request: commit_answer(
                request,
                admission,
                turn.text,
                arguments["dispositions"],
                message_id=message_id,
                finish_reason=turn.stop_reason,
                scheduler=binding.service.scheduler,
            ),
            history=[*history, receipt],
            cause=EventCause("answer.committed", "model", {"turn_id": admission.turn_id, "message_ids": [message_id]}),
        )

    # The receipt must reach the in-memory transcript before another GUI command
    # can interrupt this turn and synthesize a conflicting cancellation receipt.
    save()
    binding.facade._conversation.add_tool_result(
        parsed["tool_call_id"], COVERAGE_TOOL, {"accepted": True}, message_id=receipt_id
    )
    binding.release()
    binding.wake()
