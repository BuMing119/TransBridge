"""Production request service shares the Session command persistence boundary."""

from __future__ import annotations

from dataclasses import replace
from itertools import count

import pytest

from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.assistant_requests.transcript import TranscriptManifest
from transbridge.application.contracts import RequestContext
from transbridge.bootstrap.persistence import build_persistence_v2_services


def _build_runtime(tmp_path, *, use_cases=None):
    from tests.integration.bootstrap.test_composition import _ports
    from transbridge.bootstrap import build_runtime

    return build_runtime(
        {
            "persistence_v2_root": tmp_path / "store",
            "ui_config_path": tmp_path / "ui.ini",
            "translation_memory_root": tmp_path / "memory",
        },
        ports=_ports(),
        use_cases=use_cases,
    )


def _seed_request_job(runtime):
    from transbridge.application.assistant_requests.models import (
        AssistantExecutionRef,
        ExecutionDispatch,
        ItemKind,
        RequestItem,
        UserRequest,
    )
    from transbridge.application.tasks import JobCapabilities, JobSpec

    commands = runtime.use_cases.resolve("gui_session_commands")
    assert commands.create_and_activate("Original request", RequestContext("owner")).is_success
    snapshot = runtime.use_cases.resolve("session_lifecycle").active.aggregate.snapshot()
    context = RequestContext("owner", session_id=snapshot.ref.identity.value)
    owner = snapshot.owner
    job = runtime.tasks.submit(
        JobSpec(
            "assistant-test",
            "original-input",
            "fingerprint",
            capabilities=JobCapabilities(supports_pause=True, supports_resume=True),
            metadata=(("assistant_request_id", "request-a"),),
        ),
        owner,
    ).ref
    execution = AssistantExecutionRef("request-a", 1, ("work",), "attempt", "dispatch", "turn", context.session_id)
    request = UserRequest(
        "request-a",
        context.session_id,
        "Original work",
        (RequestItem("work", "Do work", ItemKind.EXECUTION),),
        scope=(("owner_id", "owner"), ("session_id", context.session_id)),
        dispatches=(ExecutionDispatch("dispatch", execution, job.job_id, job.run_id),),
    )
    service = commands.assistant_requests
    service.transact(context, lambda state: state.update(requests=[request.to_dict()]))
    runtime.tasks.start(job, owner)
    return service, context, owner, job


def test_bootstrap_accepts_input_and_gui_save_preserves_request_state(tmp_path):
    ids = count()
    services = build_persistence_v2_services(
        tmp_path,
        id_factory=lambda: f"id-{next(ids)}",
        timestamp_factory=lambda: "2026-09-12T00:00:00Z",
    )
    try:
        context = RequestContext("owner")
        commands = services.gui_session_commands
        assert commands.create_and_activate("Request integration", context).is_success
        ref = services.session_lifecycle.active.aggregate.ref
        context = replace(context, session_id=ref.identity.value)
        requests = commands.assistant_requests
        accepted = requests.accept_input(context, "translate selected", selection={"selected_entry_ids": ["original"]})
        assert accepted["selection"] == {"selected_entry_ids": ["original"]}
        assert commands.save_conversation(ref, [], [], context).is_success
        state = requests.state(context)
        assert state["ingress"][0]["message_id"] == accepted["message_id"]
        snapshot = services.session_lifecycle.read_session(ref, context)
        messages = requests.transcript_store.read(
            ref.identity.value, TranscriptManifest.from_dict(snapshot.transcript_data())
        )
        assert any(message.message_id == accepted["message_id"] for message in messages)
    finally:
        services.close()
    with pytest.raises(RequestError, match="closing|closed|unavailable"):
        requests.accept_input(context, "too late", selection={})


def test_actual_task_center_buttons_persist_request_waits_and_refuse_paused_request_resume(tmp_path):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    from transbridge.application.tasks import JobState
    from transbridge.ui.shell.task_center import TaskCenterController, TaskCenterPanel

    app = QApplication.instance() or QApplication([])
    runtime = _build_runtime(tmp_path)
    panel = TaskCenterPanel()
    controller = None
    try:
        service, context, owner, job = _seed_request_job(runtime)
        controller = TaskCenterController(runtime, context, panel)
        controller.start()
        assert runtime.task_projection is runtime.use_cases.resolve("task_projection")
        revision = runtime.tasks.get(job, owner).revision
        panel.pause_requested.emit(job.run_id, revision)
        app.processEvents()
        assert runtime.tasks.get(job, owner).state == JobState.PAUSED
        assert service.requests(service.state(context))[0].items[0].waiting_reasons == ("user_job_control",)
        service.command(context, "request-a", "pause", 1)
        panel.resume_requested.emit(job.run_id, runtime.tasks.get(job, owner).revision)
        app.processEvents()
        assert runtime.tasks.get(job, owner).state == JobState.PAUSED
        assert "REQUEST_PAUSED" in panel._reason.text()
        panel.cancel_requested.emit(job.run_id, runtime.tasks.get(job, owner).revision)
        assert runtime.tasks.get(job, owner).state == JobState.CANCELLING
        runtime.tasks.finish_cancelled(job, owner)
    finally:
        if controller is not None:
            controller.close()
        panel.deleteLater()
        app.processEvents()
        runtime.close()


@pytest.mark.parametrize("injected", [False, True])
def test_selected_retry_and_recovery_registries_refuse_assistant_owned_run(tmp_path, injected):
    from transbridge.application.contracts import JobRef
    from transbridge.application.tasks import JobState, TaskRecoveryIntentRegistry, TaskRetryIntentRegistry
    from transbridge.application.tasks.activity import TaskOwnerScope
    from transbridge.application.tasks.history import TaskHistoryRecord
    from transbridge.application.tasks.recovery import TaskRecoveryAvailability
    from transbridge.application.tasks.retry import TaskRetryContext

    overrides = {"task_retry_intents": TaskRetryIntentRegistry(), "task_recovery_intents": TaskRecoveryIntentRegistry()}
    runtime = _build_runtime(tmp_path, use_cases=overrides if injected else None)
    try:
        _service, context, owner, job = _seed_request_job(runtime)
        calls = []
        retry = runtime.use_cases.resolve("task_retry_intents")
        recovery = runtime.use_cases.resolve("task_recovery_intents")
        if injected:
            assert retry is overrides["task_retry_intents"]
            assert recovery is overrides["task_recovery_intents"]

        def run_again(*_args):
            calls.append(True)
            return JobRef("new-job", owner.owner_id, "new-run")

        retry.register("assistant-test", run_again)
        recovery.register("assistant-test", run_again)
        now = runtime.ports.clock.now()
        record = TaskHistoryRecord(
            job.run_id,
            job.job_id,
            TaskOwnerScope.from_owner(owner),
            "assistant-test",
            "Old task",
            JobState.FAILED,
            2,
            3,
            now,
            now,
        )
        candidate = TaskRecoveryAvailability(
            "checkpoint",
            job.run_id,
            TaskOwnerScope.from_owner(owner),
            "assistant-test",
            "Old task",
            2,
            True,
            "",
        )
        with pytest.raises(RequestError, match="REQUEST_RECOVERY_REQUIRED"):
            retry.retry(record, TaskRetryContext(owner, "current", "current-fingerprint"))
        with pytest.raises(RequestError, match="REQUEST_RECOVERY_REQUIRED"):
            recovery.recover(candidate, owner)
        assert not calls
        runtime.tasks.cancel(job, owner)
        runtime.tasks.finish_cancelled(job, owner)
    finally:
        runtime.close()
