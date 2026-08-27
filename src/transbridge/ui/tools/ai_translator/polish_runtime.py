"""Composition helpers for candidate-only proofreading runs."""

from __future__ import annotations


def create_polish_worker(
    ctx: object,
    config: object,
    entries: list,
    *,
    request_budget: object | None = None,
) -> object:
    import threading

    from transbridge.ai_translator.post_processor.proofread_pipeline import ProofreadPipeline
    from transbridge.ai_translator.term_database import TermDatabaseManager
    from transbridge.application.translation.ai_execution_profile import AiExecutionProfile
    from transbridge.infra.llm_client import create_llm_client

    from ._polish_worker import _PolishWorker
    from .workflow_log_store import WorkflowLogStore
    from .workflow_logging_client import WorkflowLoggingLLMClient

    profile = AiExecutionProfile.from_config("polish", config)
    log_store = WorkflowLogStore(ctx.esp_path, workflow="polish")
    if request_budget is None:
        from transbridge.application.translation.ai_request_budget import AiRequestBudget

        request_budget = AiRequestBudget(int(getattr(config, "max_concurrent", 1)))
    stop_event = threading.Event()
    pause_event = threading.Event()
    pause_event.set()

    def build_pipeline() -> ProofreadPipeline:
        term_manager = None
        if ctx.esp_path:
            from transbridge.ui.paratranz.target_context import bound_paratranz_project

            remote_project = bound_paratranz_project(ctx)
            paratranz_client = None
            project_id = None
            if remote_project:
                from transbridge.paratranz.api.paratranz_terms_api import ParatranzTermsAPI

                paratranz_client = ParatranzTermsAPI(ctx.config)
                project_id = remote_project["id"]
            term_manager = TermDatabaseManager(config, ctx.esp_path, paratranz_client, project_id)
            term_manager.load_all()
        llm_client = create_llm_client(config) if profile.requires_llm else None
        arbitration_llm_client = None
        if llm_client is not None:
            from transbridge.infra.limited_llm_client import LimitedLLMClient
            from transbridge.infra.llm_reasoning import ReasoningIntent, with_reasoning_intent

            provider_client = llm_client
            direct_client = with_reasoning_intent(provider_client, config, ReasoningIntent.PREFER_DIRECT)
            low_client = with_reasoning_intent(provider_client, config, ReasoningIntent.PREFER_LOW)
            llm_client = LimitedLLMClient(
                direct_client,
                request_budget,
                cancel_event=stop_event,
                pause_event=pause_event,
            )
            llm_client = WorkflowLoggingLLMClient(llm_client, log_store)
            arbitration_llm_client = LimitedLLMClient(
                low_client,
                request_budget,
                cancel_event=stop_event,
                pause_event=pause_event,
            )
            arbitration_llm_client = WorkflowLoggingLLMClient(
                arbitration_llm_client,
                log_store,
                channel_prefix="arbitration_call",
            )
        return ProofreadPipeline.create(
            profile=profile,
            llm_client=llm_client,
            arbitration_llm_client=arbitration_llm_client,
            term_manager=term_manager,
            model=config.model,
            max_tokens_per_batch=config.max_tokens_per_batch,
            max_output_tokens=config.max_output_tokens,
        )

    return _PolishWorker(
        build_pipeline,
        entries,
        max_workers=config.max_concurrent,
        profile=profile,
        log_store=log_store,
        stop_event=stop_event,
        pause_event=pause_event,
    )


__all__ = ["create_polish_worker"]
