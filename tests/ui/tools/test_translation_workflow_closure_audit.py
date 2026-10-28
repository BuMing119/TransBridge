"""Cross-boundary regressions for task-center controls after AI execution."""

from dataclasses import replace
from datetime import UTC, datetime
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.ui.tools import test_ai_task_session as session_support, test_unified_task_progress as support
from tests.ui.tools.ai_runtime_support import frozen_ai_request
from transbridge.ai_translator.project_terminology_runtime import ProjectTerminologyBinding
from transbridge.ai_translator.translation_entry_outcomes import TranslationEntryOutcome
from transbridge.application.tasks import TaskRuntime
from transbridge.application.translation.ai_execution_profile import AiExecutionProfile
from transbridge.application.translation.ai_request_budget import AiRequestBudget
from transbridge.config.llm import LLMConfig
from transbridge.ui.tools.ai_translator import task_run
from transbridge.ui.tools.ai_translator.run_controller import RunController
from transbridge.ui.tools.ai_translator.source_execution import SourceExecutor, SourceOutcome
from transbridge.ui.tools.ai_translator.task_consistency import TaskConsistency

qapp = support.qapp
make_window = support.make_window
session = session_support.session


def _bind_runtime(window, session):
    runtime = TaskRuntime(
        id_generator=SimpleNamespace(new_id=lambda: "closure-audit"),
        clock=SimpleNamespace(now=lambda: datetime.now(UTC)),
    )
    controller = RunController(task_runtime=runtime)
    request = controller.begin("polish", LLMConfig(), list(session.tasks[0].entries))
    activity = controller.create_activity(request)
    window.activity = window.run.activity = activity
    return runtime, request, controller


@pytest.mark.parametrize("cancel_point", ["before_preview", "inside_preview"])
def test_task_center_cancel_after_execution_blocks_publication(qapp, make_window, cancel_point):
    window, session, _ = make_window(polish=True, preview=True)
    runtime, request, controller = _bind_runtime(window, session)
    worker = support._start(window, session)
    worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
    support._wait_reports(qapp, window)
    assert window.run.state == "pending_confirmation"
    assert window.run.worker is None and session.commits == 0

    def cancel_from_task_center():
        runtime.cancel(request.runtime_ref, request.spec.owner)

    if cancel_point == "before_preview":
        cancel_from_task_center()

    def preview():
        if cancel_point == "inside_preview":
            cancel_from_task_center()
        return True

    window.run.apply_results(preview)
    support._wait_reports(qapp, window)
    controller.finish(request.run_id)

    assert session.commits == 0, "Task-center cancellation must fence the final AI result commit"
    assert not session.completed


def test_task_center_cancel_while_worker_runs_blocks_publication(qapp, make_window):
    """Control case: the same real runtime bridge protects the in-flight phase."""
    window, session, _ = make_window(polish=True)
    runtime, request, controller = _bind_runtime(window, session)
    worker = support._start(window, session)
    runtime.cancel(request.runtime_ref, request.spec.owner)
    worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
    support._wait_reports(qapp, window)
    controller.finish(request.run_id)
    assert session.commits == 0
    assert window.run.cancelled


def test_task_center_cancel_during_real_async_apply_never_changes_live_collection(qapp, session, monkeypatch):
    """Runtime cancellation fences the final authoritative merge mutation."""
    from transbridge.ui.version_persistence import VersionPersistence

    entered, release = threading.Event(), threading.Event()

    def held_merge():
        entered.set()
        assert release.wait(5), "test did not release authoritative result merging"

    calls = []
    session._ctx.runtime_context = object()
    session._ctx.project_commands = session_support._v2_commands(session._ctx, calls, before_merge=held_merge)
    session._persistence = VersionPersistence(session._ctx, session.version_identity)
    monkeypatch.setattr(task_run, "AiTaskWorker", support._Worker)
    runtime = TaskRuntime(
        id_generator=SimpleNamespace(new_id=lambda: "closure-audit-async"),
        clock=SimpleNamespace(now=lambda: datetime.now(UTC)),
    )
    controller = RunController(task_runtime=runtime)
    request = controller.begin("mixed", LLMConfig(), [entry for task in session.tasks for entry in task.entries])
    activity = controller.create_activity(request)
    run = task_run.AiTaskRun(request, session, activity)
    try:
        captured, errors = session_support._capture(qapp, session)
        assert captured and not errors
        run.preparing = False
        run._start(session.tasks)
        first, second = session.tasks
        entry = first.translate_entries[0]
        translation = SimpleNamespace(
            entry_outcomes={entry.identity: TranslationEntryOutcome(entry.identity, "succeeded", "审查候选译文", 2)}
        )
        run.worker.publish((SourceOutcome(first, translation=translation), SourceOutcome(second)))
        support._wait_until(entered.is_set)
        assert run.applying and session.is_busy
        runtime.cancel(request.runtime_ref, request.spec.owner)
        release.set()
        session_support._wait(qapp, session)
        support._wait_until(lambda: not run.busy)
        assert next(iter(session._ctx.collection)).translation == "", "Cancelled candidate reached live collection"
        assert not session.applied_keys
        assert not any(kind == "merge" for kind, _ in calls)
    finally:
        release.set()
        run.shutdown()
        if run.worker is not None:
            run.worker.publish(())
        session_support._wait(qapp, session)
        support._wait_until(lambda: not run.busy)
        run.preparing = False
        run.dispose()
        controller.finish(request.run_id)


@pytest.mark.parametrize("preview", [False, True])
def test_partial_preview_retry_keeps_remote_terminology_client(qapp, make_window, monkeypatch, tmp_path, preview):
    """Failed rows must retry with the same remote terminology source as attempt one."""
    window, session, _ = make_window(polish=True, preview=preview)
    client = Mock()
    client.list_terms.return_value = {"results": [{"term": "Dragon", "translation": "巨龙"}]}
    window.run.client = client
    window.run.project_id = 42
    worker_options = []

    def worker_factory(*args, **kwargs):
        worker_options.append(kwargs)
        return support._Worker(*args, **kwargs)

    monkeypatch.setattr(task_run, "AiTaskWorker", worker_factory)
    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(lambda _stem: str(tmp_path)))
    monkeypatch.setattr("transbridge.infra.llm_client.create_llm_client", Mock())
    monkeypatch.setattr(
        "transbridge.ai_translator.post_processor.proofread_pipeline.ProofreadPipeline.create",
        Mock(return_value=SimpleNamespace(process=lambda *args, **kwargs: {})),
    )
    config = LLMConfig(term_priority=["paratranz"], enable_semantic_match=False)
    request = SimpleNamespace(
        run_id="remote-terms-audit",
        config=config,
        spec=SimpleNamespace(execution_profile=AiExecutionProfile.from_config("polish", config)),
        request_budget=AiRequestBudget(1),
        terminology_binding=ProjectTerminologyBinding(),
    )
    request = frozen_ai_request(request)
    consistency = TaskConsistency(request)
    failed_source = replace(session.tasks[1], esp_path="second.esp")

    def load_terms_through_source_executor(remote_client):
        pause = threading.Event()
        pause.set()
        executor = SourceExecutor(
            request,
            stop_event=threading.Event(),
            pause_event=pause,
            shared_terms={},
            terms_lock=threading.Lock(),
            progress=lambda *args: None,
            log=lambda *args: None,
            paratranz_client=remote_client,
            project_id=42,
            consistency=consistency,
        )
        return executor._polish(failed_source, Mock())

    load_terms_through_source_executor(client)
    client.list_terms.assert_called_once_with(42, page=1, page_size=100)
    worker = support._start(window, session)
    worker.publish((SourceOutcome(session.tasks[0]), SourceOutcome(session.tasks[1], error="temporary failure")))
    support._wait_reports(qapp, window)
    if preview:
        assert window.run.state == "pending_confirmation"
        window.run.apply_results(lambda: True)
    support._wait_reports(qapp, window)
    assert window.run.can_retry
    window.run.retry()
    support._wait_until(lambda: window.run.worker is not None)
    assert len(worker_options) == 2
    # The real source composition must still load the original remote terms;
    # otherwise its consistency check makes the failed entries unretryable.
    load_terms_through_source_executor(worker_options[1]["client"])
    assert worker_options[1]["client"] is client, "Retry lost its remote terminology source after preview"
    client.close.assert_not_called()
