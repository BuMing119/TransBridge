"""Compatibility wrapper for the shared Smart Assistant workflow runtime."""

from __future__ import annotations

from ._workflow_llm_runtime import WorkflowLlmRuntime, create_workflow_llm_runtime

PolishLlmRuntime = WorkflowLlmRuntime


def create_polish_llm_runtime(llm_config: object, *, esp_path: str, stop_event: object) -> PolishLlmRuntime:
    """Create one budgeted and logged client for the complete polish run."""

    return create_workflow_llm_runtime(
        llm_config,
        esp_path=esp_path,
        workflow="polish",
        stop_event=stop_event,
    )


__all__ = ["PolishLlmRuntime", "create_polish_llm_runtime"]
