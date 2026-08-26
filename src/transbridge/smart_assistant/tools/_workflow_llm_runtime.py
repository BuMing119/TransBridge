"""Shared budgeted and logged LLM runtime for Smart Assistant workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from transbridge.application.translation.ai_request_budget import AiRequestBudget
    from transbridge.infra.llm_client import LLMClient
    from transbridge.ui.tools.ai_translator.workflow_log_store import WorkflowLogStore


@dataclass(frozen=True, slots=True)
class WorkflowLlmRuntime:
    """Resources shared by every LLM call in one Smart Assistant workflow."""

    client: LLMClient
    request_budget: AiRequestBudget
    log_store: WorkflowLogStore

    def close(self) -> None:
        self.log_store.close()


def create_workflow_llm_runtime(
    llm_config: object,
    *,
    esp_path: str,
    workflow: str,
    stop_event: object,
    pause_event: object | None = None,
) -> WorkflowLlmRuntime:
    """Create one budgeted, pausable and logged client for a complete workflow."""

    from transbridge.application.translation.ai_request_budget import AiRequestBudget
    from transbridge.infra.limited_llm_client import LimitedLLMClient
    from transbridge.infra.llm_client import create_llm_client
    from transbridge.ui.tools.ai_translator.workflow_log_store import WorkflowLogStore
    from transbridge.ui.tools.ai_translator.workflow_logging_client import WorkflowLoggingLLMClient

    max_concurrent = int(getattr(llm_config, "max_concurrent", 1))
    request_budget = AiRequestBudget(max(1, max_concurrent))
    log_store = WorkflowLogStore(esp_path, workflow=workflow)
    provider_client = create_llm_client(llm_config)
    limited_client = LimitedLLMClient(
        provider_client,
        request_budget,
        cancel_event=stop_event,
        pause_event=pause_event,
    )
    client = WorkflowLoggingLLMClient(limited_client, log_store)
    return WorkflowLlmRuntime(client, request_budget, log_store)


__all__ = ["WorkflowLlmRuntime", "create_workflow_llm_runtime"]
