"""Qt receipt projection for application-owned control operations."""

from dataclasses import dataclass, field
import json
from threading import Event

from PyQt6.QtCore import QTimer

from transbridge.application.assistant_requests.control_operations import run_control_call
from transbridge.application.assistant_requests.routing_results import ROUTING_TOOL, run_routing_call

from .message_bubble import MessageBubble


@dataclass
class PendingControl:
    context: object
    admission: object
    cancelled: Event = field(default_factory=Event)


class RequestControlBinding:
    def __init__(self, binding):
        self.binding = binding
        self.pending = {}

    def cancel(self):
        for operation in self.pending.values():
            operation.cancelled.set()

    def start(self, parsed, *, stop_reason=None):
        binding = self.binding
        context, admission = binding.context, binding.admission
        identity = parsed["tool_call_id"]
        operation = PendingControl(context, admission)
        if identity in self.pending:
            raise ValueError("Control operation is already in progress")
        batch_id = binding.batch.batch_id if parsed["control"] == ROUTING_TOOL else ""
        parent = binding.facade._conversation.reserve_control_call(identity, admission.turn_id, batch_id=batch_id)
        history = binding.facade._conversation.get_transcript() if batch_id else None
        self.pending[identity] = operation

        def work():
            if parsed["control"] == ROUTING_TOOL:
                return run_routing_call(
                    binding.service,
                    context,
                    admission,
                    batch_id,
                    parsed,
                    parent,
                    cancelled=operation.cancelled.is_set,
                    history=history,
                )
            return run_control_call(
                binding.service,
                context,
                admission,
                parsed,
                parent,
                stop_reason=stop_reason,
                cancelled=operation.cancelled.is_set,
            )

        def received(receipt):
            self.pending.pop(identity, None)
            if binding.context != context:
                return
            if operation.cancelled.is_set() and not binding.facade._conversation.contains_tool_call(identity):
                return  # Reload's authoritative merge delivers this saved receipt.
            binding.facade._conversation.apply_control_receipt(receipt)
            if parsed["control"] == ROUTING_TOOL and not receipt.get("is_error"):
                from transbridge.application.assistant_requests.routing_commit import direct_replies

                batch = json.loads(receipt["content"])
                known = {message["message_id"] for message in binding.facade._conversation.get_transcript()}
                for reply in direct_replies(batch):
                    if reply["message_id"] not in known:
                        binding.facade._conversation.add_assistant(reply["content"], message_id=reply["message_id"])
                        binding.facade._message_list.add_bubble(
                            MessageBubble(reply["content"], "assistant", theme=binding.facade._theme)
                        )
            if operation.cancelled.is_set() or binding.admission != admission or not binding._active:
                return
            if receipt.get("is_error"):
                binding.fail(f"请求控制未接纳：{receipt['content']}")
                return
            if parsed["control"] in {"report_answer_coverage", ROUTING_TOOL}:
                if parsed["control"] == ROUTING_TOOL:
                    batch = json.loads(receipt["content"])
                    binding._priority = tuple(
                        result["request_id"] for result in batch.get("receipts", ()) if result["status"] == "applied"
                    )
                binding.release()
                binding.wake()
            else:

                def resume():
                    if not binding._closed and binding._active and binding.admission == admission:
                        binding.facade._orchestrator.start_round()

                QTimer.singleShot(0, resume)

        def failed(exc):
            self.pending.pop(identity, None)
            if binding.context == context:
                binding.fail(f"控制结果未能保存：{exc}")

        binding.background.submit(work, received, failed=failed)
