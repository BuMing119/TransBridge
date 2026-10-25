"""Compose the shared proofreading pipeline for any task source."""

from __future__ import annotations

from contextlib import nullcontext


def build_proofread_pipeline(
    config,
    esp_path,
    *,
    profile,
    request_budget,
    terminology_binding,
    stop_event,
    pause_event,
    log_store,
    paratranz_client=None,
    project_id=None,
    term_snapshot_observer=None,
    terminology_access=None,
    checkpoint=None,
    shared_term_pool=None,
    generated_term_plugin_id=None,
    request_lane=None,
    reference_store=None,
):
    from transbridge.ai_translator.post_processor.proofread_pipeline import ProofreadPipeline
    from transbridge.ai_translator.term_database import TermDatabaseManager
    from transbridge.infra.limited_llm_client import LimitedLLMClient
    from transbridge.infra.llm_client import create_llm_client
    from transbridge.infra.llm_reasoning import ReasoningIntent, with_reasoning_intent

    from .source_terms import source_terminology_path
    from .workflow_logging_client import WorkflowLoggingLLMClient

    term_manager = None
    if esp_path or shared_term_pool is not None:
        term_manager = TermDatabaseManager(
            config,
            source_terminology_path(esp_path, request_lane),
            paratranz_client,
            project_id,
            shared_term_pool=shared_term_pool,
            generated_term_plugin_id=generated_term_plugin_id,
            reference_store=reference_store,
            reference_source=request_lane,
            **(terminology_binding.term_database_kwargs() if terminology_binding is not None else {}),
        )
        with (terminology_access or nullcontext)():
            term_manager.load_all()
            if term_snapshot_observer is not None:
                term_snapshot_observer(term_manager)
    llm_client = arbitration = None
    if profile.requires_llm:
        provider = create_llm_client(config)

        def wrap(intent, channel):
            client = LimitedLLMClient(
                with_reasoning_intent(provider, config, intent),
                request_budget,
                cancel_event=stop_event,
                pause_event=pause_event,
                lane_key=(request_lane or esp_path, channel),
            )
            return WorkflowLoggingLLMClient(client, log_store, channel_prefix=channel)

        llm_client = wrap(ReasoningIntent.PREFER_DIRECT, "proofread_call")
        arbitration = wrap(ReasoningIntent.PREFER_LOW, "arbitration_call")
    return ProofreadPipeline.create(
        profile=profile,
        llm_client=llm_client,
        arbitration_llm_client=arbitration,
        term_manager=term_manager,
        model=config.model,
        max_tokens_per_batch=config.max_tokens_per_batch,
        max_output_tokens=config.max_output_tokens,
        checkpoint=checkpoint,
    )
