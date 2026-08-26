"""Composition helpers for candidate-only proofreading runs."""

from __future__ import annotations


def create_polish_worker(ctx: object, config: object, entries: list) -> object:
    from transbridge.ai_translator.post_processor.proofread_pipeline import ProofreadPipeline
    from transbridge.ai_translator.term_database import TermDatabaseManager
    from transbridge.application.translation.ai_execution_profile import AiExecutionProfile
    from transbridge.infra.llm_client import create_llm_client

    from ._polish_worker import _PolishWorker

    def build_pipeline() -> ProofreadPipeline:
        profile = AiExecutionProfile.from_config("polish", config)
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
        return ProofreadPipeline.create(
            profile=profile,
            llm_client=create_llm_client(config) if profile.requires_llm else None,
            term_manager=term_manager,
        )

    return _PolishWorker(build_pipeline, entries, max_workers=config.max_concurrent)


__all__ = ["create_polish_worker"]
