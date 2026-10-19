"""Recover one omitted answer declaration without inferring task completion."""

from dataclasses import replace

from PyQt6.QtCore import QTimer

from transbridge.application.assistant_requests.models import ItemKind
from transbridge.smart_assistant.request_protocol import COVERAGE_TOOL


class RequestAnswerBinding:
    def __init__(self, binding):
        self.binding = binding
        self._repair_request = None
        self._repair_source = None

    def reset(self):
        self._repair_request = None
        self._repair_source = None

    def accept(self, parsed, turn):
        self.binding.control_results.start(parsed, stop_reason=turn.stop_reason)

    def recover(self, turn):
        """Ask once for actual coverage; interrupted/empty/execution-only turns fail normally."""
        binding = self.binding
        admission = binding.admission
        if (
            admission is None
            or admission.stage != "execution"
            or turn.stop_reason not in {"stop", "end_turn"}
            or not turn.text.strip()
            or turn.tool_calls
        ):
            return False
        key = (admission.session_id, admission.request_id, admission.request_revision, admission.request_lease_epoch)
        if self._repair_request == key:
            return False
        binding.service.scheduler.validate(admission)
        request = next(
            r
            for r in binding.service.requests(binding.service.state(binding.context))
            if r.request_id == admission.request_id
        )
        if not any(i.kind == ItemKind.ANSWER and i.item_id in admission.ready_item_ids for i in request.items):
            return False
        self._repair_request = key
        self._repair_source = binding.facade._conversation.get_transcript()[-1]

        def resume():
            if not binding._closed and binding._active and not binding._user_stopped and binding.admission == admission:
                binding.facade._orchestrator.start_round()

        QTimer.singleShot(0, resume)
        return True

    def duplicate_of(self, turn):
        """Hide only exact repetitions from this declaration repair, never new content."""
        source = self._repair_source
        if (
            source is not None
            and len(turn.tool_calls) == 1
            and turn.tool_calls[0].name == COVERAGE_TOOL
            and turn.text == source.get("content")
        ):
            return source["message_id"]
        return ""

    def tools(self, definitions):
        if self._repair_request is None:
            return definitions
        # Keep the correction in this admitted continuation's native protocol.
        # It is neither user input nor an assertion that the prior text was sufficient.
        correction = (
            " Your previous plain-text reply omitted this declaration. Check the current ready answer items: "
            "if the reply answers them, submit the complete answer and only its true coverage now. "
            "Otherwise perform the missing work using business tools, or mark an answer item blocked when "
            "user input is needed. Do not repeat a plain-text-only reply or claim unfinished work completed."
        )
        return tuple(
            replace(tool, description=tool.description + correction) if tool.name == COVERAGE_TOOL else tool
            for tool in definitions
        )
