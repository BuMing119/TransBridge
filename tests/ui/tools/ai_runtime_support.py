"""Real task authority for AI controller tests."""

from copy import copy
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from transbridge.application.tasks import TaskRuntime


def ai_task_runtime():
    return TaskRuntime(
        id_generator=SimpleNamespace(new_id=lambda: uuid4().hex),
        clock=SimpleNamespace(now=lambda: datetime.now(UTC)),
    )


def admit_ai_request(request, tasks=(), *, identity=None):
    """Build an admitted production request while preserving fixture intent."""
    from transbridge.ai_translator.project_terminology_runtime import ProjectTerminologyBinding
    from transbridge.config.llm import LLMConfig
    from transbridge.ui.tools.ai_translator.run_controller import RunController

    config = copy(getattr(request, "config", LLMConfig()))
    config.max_concurrent = getattr(config, "max_concurrent", 1)
    spec = request.spec
    config.polish_preview_enabled = (
        getattr(spec.execution_profile, "preview_enabled", False) if hasattr(spec, "execution_profile") else False
    )
    owner = getattr(spec, "owner", None)
    project_id, variant_id = identity or (getattr(owner, "project_id", None), getattr(owner, "variant_id", None))
    runtime = TaskRuntime(
        id_generator=SimpleNamespace(new_id=lambda: request.run_id),
        clock=SimpleNamespace(now=lambda: datetime.now(UTC)),
    )
    controller = RunController(task_runtime=runtime)
    admitted = controller.begin(
        getattr(spec, "mode", getattr(getattr(spec, "execution_profile", None), "preset", "mixed")),
        config,
        [entry for task in tasks for entry in task.entries],
        project_id=project_id,
        variant_id=variant_id,
        overwrite=getattr(spec, "overwrite", False),
    )
    admitted = replace(
        admitted, terminology_binding=getattr(request, "terminology_binding", ProjectTerminologyBinding())
    )
    activity = controller.create_activity(admitted)
    controller.finish(admitted.run_id)
    return admitted, activity


def frozen_ai_request(request):
    from transbridge.ui.tools.ai_translator.run_controller import TranslationRunRequest

    if isinstance(request, TranslationRunRequest):
        return request
    return admit_ai_request(request)[0]
