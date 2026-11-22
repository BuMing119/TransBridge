# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Connect an admitted background task to the desktop translation worker."""

from dataclasses import replace
import logging

logger = logging.getLogger(__name__)


def execute_translation_sources(
    *,
    source,
    config,
    mode,
    owner,
    result_id,
    directory,
    terminology,
    remote_config,
    remote_project,
    stop_event,
    pause_event,
    progress_callback,
):
    """Run the frozen source on the owning task's thread, with its controls and result directory."""
    from .tools.ai_translator.run_request_preparation import prepare_run_request
    from .tools.ai_translator.task_consistency import TaskConsistency
    from .tools.ai_translator.task_worker import AiTaskWorker

    prepared = prepare_run_request(
        owner.owner_id,
        mode,
        config,
        source.entries,
        entrypoint="smart-assistant",
        project_id=owner.project_id,
        variant_id=owner.variant_id,
        session_id=owner.session_id,
        run_id=result_id,
        esp_path=source.esp_path,
        restored_terminology_binding=terminology,
    )
    request = replace(prepared.execution_request(), recovery_directory=directory)
    consistency = TaskConsistency(request)
    client = None
    if remote_project is not None and remote_config is not None:
        from transbridge.paratranz.api.paratranz_terms_api import ParatranzTermsAPI

        client = ParatranzTermsAPI(remote_config)
    try:
        worker = AiTaskWorker(
            request,
            (source,),
            client=client,
            project_id=remote_project,
            consistency=consistency,
            attempt=1,
            stop_event=stop_event,
            pause_event=pause_event,
            progress_callback=progress_callback,
        )
        return worker.execute()
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                logger.exception("关闭任务的 ParaTranz 术语连接失败")
