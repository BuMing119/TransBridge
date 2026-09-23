"""Detached model-input snapshots assembled without UI or persistence access."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from transbridge.application.assistant_requests.history_scope import assign_history_requests
from transbridge.application.assistant_requests.models import UserRequest, digest
from transbridge.application.assistant_requests.summaries import RequestSummary
from transbridge.infra.llm_tool_calling import LlmToolDefinition

from .context_budget import ContextBudget


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
        from transbridge.application.assistant_context.models import CompactionSummary
        from transbridge.application.assistant_context.projection import append_context

        summaries = ()
        if self.prepared_summary is not None and self.prepared_summary[1] == digest(request.to_dict()):
            summary = self.prepared_summary[0]
            if summary is not None:
                summaries = (
                    CompactionSummary(
                        "legacy-" + digest(summary.to_dict()), summary.text, (), summary.source_ids, "legacy-excerpt"
                    ),
                )
        epoch = append_context(
            history,
            request,
            dict(self.request_state or {}),
            config_digest="detached",
            owners=self.message_owners,
            summaries=summaries,
        )
        self.budget.require(epoch.messages, self.tools)
        return epoch.messages, self.tools
