"""Real permit arbitration, GUI delivery and run-owned resource regressions."""

from datetime import UTC, datetime
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.application.projects.test_gui_facade_ai_snapshots import _facade
from tests.ui.tools import test_ai_task_session as session_support, test_unified_task_progress as support
from transbridge.ai_translator.translation_entry_outcomes import TranslationEntryOutcome
from transbridge.application.projects import ActiveProject, ProjectLifecycleService
from transbridge.application.projects.gui_facade import GuiProjectCommandFacade
from transbridge.application.tasks import JobState, TaskRuntime, TransitionError
from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui.tools.ai_translator import task_run
from transbridge.ui.tools.ai_translator.run_controller import RunController
from transbridge.ui.tools.ai_translator.source_execution import SourceOutcome
from transbridge.ui.version_persistence import VersionPersistence

qapp = support.qapp
session = session_support.session
make_window = support.make_window


def _runtime():
    return TaskRuntime(
        id_generator=SimpleNamespace(new_id=lambda: "permit-boundary"),
        clock=SimpleNamespace(now=lambda: datetime.now(UTC)),
    )


@pytest.fixture
def active_run(qapp, session, monkeypatch, request):
    monkeypatch.setattr(task_run, "AiTaskWorker", support._Worker)
    runtime = _runtime()
    controller = RunController(task_runtime=runtime)
    mode = getattr(request, "param", "mixed")
    request = controller.begin(mode, LLMConfig(), [entry for task in session.tasks for entry in task.entries])
    activity = controller.create_activity(request)
    client = Mock()
    run = task_run.AiTaskRun(request, session, activity, client=client)
    try:
        captured, errors = session_support._capture(qapp, session)
        assert captured and not errors
        run.preparing = False
        run._start(session.tasks)
        yield SimpleNamespace(runtime=runtime, request=request, run=run, session=session, client=client)
    finally:
        run.shutdown()
        if run.worker is not None:
            run.worker.publish(())
        session_support._wait(qapp, session)
        support._wait_reports(qapp, SimpleNamespace(run=run))
        run.preparing = False
        run.dispose()
        controller.finish(request.run_id)


def _outcomes(session):
    first, second = session.tasks
    entry = first.entries[0]
    translated = SimpleNamespace(
        entry_outcomes={entry.identity: TranslationEntryOutcome(entry.identity, "succeeded", "新译文", 2)}
    )
    return SourceOutcome(first, translation=translated), SourceOutcome(second)


def test_cancel_after_commit_keeps_applied_ledger_and_reaches_cancelled(qapp, active_run):
    item = active_run

    def cancel_after_commit(event):
        if event.code == "commit_accepted":
            item.runtime.cancel(item.request.runtime_ref, item.request.spec.owner)

    subscription = item.runtime.subscribe(cancel_after_commit)
    try:
        item.run.worker.publish(_outcomes(item.session))
        session_support._wait(qapp, item.session)
        support._wait_reports(qapp, SimpleNamespace(run=item.run))
        assert next(iter(item.session._ctx.collection)).translation == "新译文"
        assert item.run.entries.applied_keys == item.session.applied_keys
        assert len(item.session.applied_keys) == 2
        assert item.runtime.get(item.request.runtime_ref, item.request.spec.owner).state is JobState.CANCELLED
        assert item.run.state == "cancelled"
        assert item.run.records.record["state"] == "cancelled"
        assert item.run.records.record["applied"]
    finally:
        subscription.close()


def test_external_cancel_is_delivered_on_gui_thread_and_waits_for_worker_release(qapp, active_run, monkeypatch):
    item = active_run
    worker = item.run.worker
    gui_thread = threading.get_ident()
    deliveries = []
    rollback = item.session.rollback_uncommitted

    def observed_rollback():
        deliveries.append(threading.get_ident())
        rollback()

    monkeypatch.setattr(item.session, "rollback_uncommitted", observed_rollback)
    thread = threading.Thread(target=lambda: item.runtime.cancel(item.request.runtime_ref, item.request.spec.owner))
    thread.start()
    thread.join(2)
    assert not thread.is_alive()
    assert not deliveries
    qapp.processEvents()
    assert deliveries and set(deliveries) == {gui_thread}
    assert item.run.cancelled and worker.was_cancelled
    item.client.close.assert_not_called()
    worker.publish(_outcomes(item.session))
    support._wait_reports(qapp, SimpleNamespace(run=item.run))
    item.run.cancel()
    item.run.cancel()
    qapp.processEvents()
    item.client.close.assert_called_once_with()
    assert not item.session.applied_keys
    assert item.runtime.get(item.request.runtime_ref, item.request.spec.owner).state is JobState.CANCELLED


def test_mutation_exception_is_failed_and_never_records_applied(qapp, active_run, monkeypatch):
    item = active_run
    guarded_commit = item.run.activity.commit_mutation

    def fail_commit(_mutation):
        def fail():
            raise ValueError("injected aggregate failure")

        guarded_commit(fail)

    monkeypatch.setattr(item.session, "commit_mutation", fail_commit)
    item.run.worker.publish(_outcomes(item.session))
    session_support._wait(qapp, item.session)
    support._wait_reports(qapp, SimpleNamespace(run=item.run))
    assert item.runtime.get(item.request.runtime_ref, item.request.spec.owner).state is JobState.FAILED
    assert item.run.state == "failed" and not item.run.cancelled
    assert not item.run.entries.applied_keys and not item.session.applied_keys
    assert next(iter(item.session._ctx.collection)).translation == ""
    item.client.close.assert_called_once_with()
    item.run.shutdown()
    assert item.run.state == "failed" and not item.run.cancelled


@pytest.mark.parametrize("active_run", ["translate"], indirect=True)
def test_paused_application_can_be_retried_after_resume(qapp, active_run):
    item = active_run
    item.run.worker.publish(_outcomes(item.session))
    item.runtime.pause(item.request.runtime_ref, item.request.spec.owner)
    session_support._wait(qapp, item.session)
    assert not item.session.applied_keys
    assert item.run.can_apply_success and not item.run.blocked
    item.runtime.resume(item.request.runtime_ref, item.request.spec.owner)
    item.run.apply_results()
    session_support._wait(qapp, item.session)
    support._wait_reports(qapp, SimpleNamespace(run=item.run))
    assert len(item.session.applied_keys) == 2
    assert item.run.state == "completed"
    item.client.close.assert_called_once_with()


def test_hidden_preview_and_repeated_partial_attempts_keep_client_until_completion(qapp, make_window):
    window, session, _ = make_window(polish=True, preview=True)
    client = Mock()
    window.run.client = client
    worker = support._start(window, session)
    window.close()
    assert not window.run.cancelled
    worker.publish((SourceOutcome(session.tasks[0]), SourceOutcome(session.tasks[1], error="temporary failure")))
    window.run.apply_results(lambda: True)
    support._wait_reports(qapp, window)
    client.close.assert_not_called()
    for _ in range(2):
        window.run.retry()
        retried = window.run.worker
        retried.publish(tuple(SourceOutcome(task, error="temporary failure") for task in retried.tasks))
        support._wait_reports(qapp, window)
        client.close.assert_not_called()
    window.run.retry()
    retried = window.run.worker
    retried.publish(tuple(SourceOutcome(task) for task in retried.tasks))
    client.close.assert_not_called()
    window.run.apply_results(lambda: True)
    support._wait_reports(qapp, window)
    assert window.run.state == "completed"
    assert len(window.run.entries.applied_keys) == 2
    client.close.assert_called_once_with()
    window.run.dispose()
    client.close.assert_called_once_with()


@pytest.mark.parametrize("state", ["paused", "cancel_between_permit_and_mutation", "progress_invalidates_permit"])
def test_real_project_publication_rejects_inactive_or_stale_permit(state, monkeypatch):
    _unused, fixture_lifecycle, aggregate, key, context, _projections = _facade()
    events = []
    lifecycle = ProjectLifecycleService(
        None,
        None,
        active=ActiveProject(fixture_lifecycle.active.project, aggregate, aggregate.ref, 0, 0),
        event_publisher=events.append,
    )
    runtime = _runtime()
    controller = RunController(task_runtime=runtime)
    request = controller.begin("translate", LLMConfig(), [])
    activity = controller.create_activity(request)
    ctx = SimpleNamespace(
        active_version_identity=("project", "variant"),
        project_revision=0,
        variant_revision=0,
        uses_authoritative_projection=True,
        project_commands=GuiProjectCommandFacade(lifecycle, None),
        runtime_context=context,
    )
    persistence = VersionPersistence(ctx, ctx.active_version_identity)
    entry = TranslationEntry("entry", "entry", "Source", "译文", 2, "NPC_:FULL", entry_key=key)
    original = runtime.try_commit

    def race(permit, mutation):
        if state == "cancel_between_permit_and_mutation":
            runtime.cancel(request.runtime_ref, request.spec.owner)
        else:
            runtime.update_progress(request.runtime_ref, request.spec.owner, {"current": 1})
        return original(permit, mutation)

    try:
        if state == "paused":
            runtime.pause(request.runtime_ref, request.spec.owner)
        else:
            monkeypatch.setattr(runtime, "try_commit", race)
        result = persistence.commit_translation((entry,), commit_mutation=activity.commit_mutation)
        assert not result.is_success
        assert aggregate.revision == lifecycle.generation == 0
        assert aggregate.snapshot().entries[0].translation == ""
        assert events == []
    finally:
        activity.close()
        controller.finish(request.run_id)


def test_project_notification_runs_after_runtime_lock_is_released():
    _unused, fixture_lifecycle, aggregate, key, context, _projections = _facade()
    runtime = _runtime()
    controller = RunController(task_runtime=runtime)
    request = controller.begin("translate", LLMConfig(), [])
    activity = controller.create_activity(request)
    observed = []

    def notify(_event):
        thread = threading.Thread(target=lambda: runtime.cancel(request.runtime_ref, request.spec.owner))
        thread.start()
        thread.join(2)
        observed.append(not thread.is_alive())

    lifecycle = ProjectLifecycleService(
        None,
        None,
        active=ActiveProject(fixture_lifecycle.active.project, aggregate, aggregate.ref, 0, 0),
        event_publisher=notify,
    )
    facade = GuiProjectCommandFacade(lifecycle, None)
    try:
        result = facade.replace_entry_states({key: ("译文", 2)}, context, commit_mutation=activity.commit_mutation)
        assert result.is_success
        assert observed == [True], "Projection notification held the runtime cancellation lock"
        assert aggregate.snapshot().entries[0].translation == "译文"
        assert aggregate.revision == lifecycle.generation == 1
    finally:
        activity.close()
        controller.finish(request.run_id)


def test_paused_adapter_does_not_issue_a_commit_permit():
    runtime = _runtime()
    controller = RunController(task_runtime=runtime)
    request = controller.begin("translate", LLMConfig(), [])
    activity = controller.create_activity(request)
    calls = []
    try:
        runtime.pause(request.runtime_ref, request.spec.owner)
        with pytest.raises(TransitionError, match="paused"):
            activity.commit_mutation(lambda: calls.append(True))
        assert calls == []
    finally:
        activity.close()
        controller.finish(request.run_id)
