"""Composition helpers for candidate-only proofreading runs."""

from __future__ import annotations


def create_polish_worker(
    ctx: object,
    config: object,
    entries: list,
    *,
    request_budget: object | None = None,
    terminology_binding: object | None = None,
) -> object:
    import threading

    from transbridge.application.translation.ai_execution_profile import AiExecutionProfile

    from ._polish_worker import _PolishWorker
    from .workflow_log_store import WorkflowLogStore

    profile = AiExecutionProfile.from_config("polish", config)
    log_store = WorkflowLogStore(ctx.esp_path, workflow="polish")
    if request_budget is None:
        from transbridge.application.translation.ai_request_budget import AiRequestBudget

        request_budget = AiRequestBudget(int(getattr(config, "max_concurrent", 1)))
    stop_event = threading.Event()
    pause_event = threading.Event()
    pause_event.set()
    if terminology_binding is None:
        from transbridge.ai_translator.project_terminology_runtime import ProjectTerminologyBinding

        terminology_binding = ProjectTerminologyBinding()

    def build_pipeline():
        from transbridge.ui.paratranz.target_context import bound_paratranz_project

        from .proofread_composition import build_proofread_pipeline

        remote = bound_paratranz_project(ctx)
        client = None
        if remote:
            from transbridge.paratranz.api.paratranz_terms_api import ParatranzTermsAPI

            client = ParatranzTermsAPI(ctx.config)
        return build_proofread_pipeline(
            config,
            ctx.esp_path,
            profile=profile,
            request_budget=request_budget,
            terminology_binding=terminology_binding,
            stop_event=stop_event,
            pause_event=pause_event,
            log_store=log_store,
            paratranz_client=client,
            project_id=remote["id"] if remote else None,
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
