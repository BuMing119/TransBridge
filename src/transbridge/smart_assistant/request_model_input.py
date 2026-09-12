"""Detached model-input snapshots assembled without UI or persistence access."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from transbridge.application.assistant_requests.models import UserRequest, digest
from transbridge.application.assistant_requests.summaries import RequestSummary
from transbridge.infra.llm_tool_calling import LlmToolDefinition

from .context_budget import ContextBudget
from .request_context_assembler import RequestContextAssembler, assign_history_requests


@dataclass(frozen=True)
class RequestModelInput:
    """Transfer detached snapshot ownership to a worker, never a live Qt object.

    Fields cannot be rebound. Nested history/schema dictionaries are snapshots
    owned by this preparation; assembly projects them without modifying them.
    """

    history: tuple[Mapping[str, Any], ...]
    tools: tuple[LlmToolDefinition, ...]
    budget: ContextBudget
    request: UserRequest | None = None
    request_state: Mapping[str, Any] | None = None
    requests: tuple[UserRequest, ...] = ()
    message_owners: Mapping[str, str] = field(default_factory=dict)
    prepared_summary: tuple[RequestSummary | None, str] | None = None

    def assemble(self):
        if self.request is None:
            messages = list(self.history)
            self.budget.require(messages, self.tools)
            return messages, self.tools
        request = self.request
        history = assign_history_requests(self.history, self.requests, self.message_owners)
        sources = set(request.source_message_ids) | {revision.source_message_id for revision in request.revisions}
        current_input = next((m["message_id"] for m in reversed(history) if m.get("message_id") in sources), None)
        summary = None
        if self.prepared_summary is not None and self.prepared_summary[1] == digest(request.to_dict()):
            summary = self.prepared_summary[0]
        projection = RequestContextAssembler(self.budget).assemble(
            history,
            tools=self.tools,
            request_state=self.request_state,
            current_input_id=current_input,
            continuation={"reason": "continue admitted request", "request_id": request.request_id},
            summary=summary,
        )
        return projection.messages, self.tools
