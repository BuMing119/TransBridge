"""Read history off the Qt thread and deliver only to the still-admitted request."""

import logging

from PyQt6.QtCore import QTimer

from transbridge.application.assistant_context.history_queries import read_request_history, validate_history_source
from transbridge.application.assistant_requests.admission import validate_turn
from transbridge.application.assistant_requests.models import RequestError
from transbridge.smart_assistant.request_protocol import HISTORY_RETRIEVAL_TOOL, parse_history_retrieval

logger = logging.getLogger(__name__)


def retrieve_history(binding, parsed):
    arguments = parse_history_retrieval(parsed["arguments"])
    admission, context = binding.admission, binding.context

    def validate():
        state = binding.service.state(context)
        request = next((r for r in binding.service.requests(state) if r.request_id == admission.request_id), None)
        if request is None or state.get("session_tombstone"):
            raise RequestError("TURN_LEASE_STALE", "history request is no longer available")
        validate_turn(request, admission, tool_name=HISTORY_RETRIEVAL_TOOL, scheduler=binding.service.scheduler)
        validate_history_source(state, request, arguments["message_id"])

    validate()

    def read():
        validate()
        return read_request_history(binding.service, context, admission.request_id, **arguments)

    def display(completed):
        if binding._closed or not binding._active or binding.context != context or binding.admission != admission:
            return
        try:
            result = completed.result()
            validate()
            conversation = binding.facade._conversation
            conversation.add_tool_result(parsed["tool_call_id"], HISTORY_RETRIEVAL_TOOL, result)
            binding.service.save_history(context, conversation.get_transcript(), request_id=admission.request_id)

            def resume():
                if (
                    not binding._closed
                    and binding._active
                    and binding.context == context
                    and binding.admission == admission
                ):
                    binding.facade._orchestrator.start_round()

            QTimer.singleShot(0, resume)
        except Exception as exc:
            binding.facade._conversation.close_pending_tool_calls(f"历史查询未接纳：{exc}")
            binding.fail(str(exc))

    future = binding._queue.submit(read)

    def completed(result):
        try:
            binding.delivered.emit(lambda: display(result))
        except RuntimeError:
            logger.info("History query finished after the request view was destroyed")

    future.add_done_callback(completed)
