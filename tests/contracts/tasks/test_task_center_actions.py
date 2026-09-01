from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from transbridge.application.contracts import JobRef
from transbridge.application.tasks import (
    InMemoryTaskHistoryPort,
    JobState,
    OwnerRef,
    TaskArtifactRef,
    TaskCenterAction,
    TaskCenterActionError,
    TaskCenterActions,
    TaskHistoryNavigationRegistry,
    TaskHistoryRecord,
    TaskNavigationIntent,
    TaskOwnerScope,
    TaskRecoveryAvailability,
    TaskRecoveryIntentRegistry,
    TaskRetryContext,
    TaskRetryIntentRegistry,
)


class _RecoveryCatalog:
    def __init__(self, records=()) -> None:
        self.records = tuple(records)

    def list(self, _actor):
        return self.records


class _Navigator:
    def resolve(self, _record, _actor, action):
        return TaskNavigationIntent(f"task.{action.value}")


def _history_record(owner: OwnerRef) -> TaskHistoryRecord:
    now = datetime(2026, 8, 24, tzinfo=UTC)
    return TaskHistoryRecord(
        run_id="run-old",
        job_id="job-old",
        owner=TaskOwnerScope.from_owner(owner),
        job_type="translation",
        display_name="AI 翻译",
        state=JobState.FAILED,
        terminal_revision=4,
        terminal_sequence=9,
        created_at=now,
        finished_at=now,
        artifact_refs=(TaskArtifactRef("opaque-report", "report"),),
    )


def test_task_center_actions_default_to_unavailable_even_when_history_has_artifacts() -> None:
    owner = OwnerRef("owner-one", "gui", project_id="project-one")
    history = InMemoryTaskHistoryPort()
    history.append(_history_record(owner))
    actions = TaskCenterActions(
        history,
        _RecoveryCatalog(),
        TaskRetryIntentRegistry(),
        TaskRecoveryIntentRegistry(),
        TaskHistoryNavigationRegistry(),
    )

    item = actions.list_history(owner, retry_context=None)[0]

    assert not item.available_actions.retry
    assert not item.available_actions.open_result
    assert not item.available_actions.open_log


def test_task_center_actions_route_registered_handlers_and_reject_stale_records() -> None:
    owner = OwnerRef("owner-one", "gui", project_id="project-one")
    history = InMemoryTaskHistoryPort()
    history.append(_history_record(owner))
    recovery_record = TaskRecoveryAvailability(
        storage_key="checkpoint-one",
        run_id="run-old",
        owner=TaskOwnerScope.from_owner(owner),
        job_type="translation",
        display_name="AI 翻译",
        checkpoint_revision=2,
        recoverable=True,
        reason_code="recoverable",
    )
    recovery = _RecoveryCatalog((recovery_record,))
    retries = TaskRetryIntentRegistry()
    retries.register("translation", lambda _previous, _context: JobRef("job-new", owner.owner_id, "run-new"))
    recoveries = TaskRecoveryIntentRegistry()
    recoveries.register("translation", lambda _candidate, _actor: JobRef("job-recovered", owner.owner_id, "run-old"))
    navigators = TaskHistoryNavigationRegistry()
    navigators.register("translation", _Navigator())
    actions = TaskCenterActions(history, recovery, retries, recoveries, navigators)
    retry_context = TaskRetryContext(owner, "project:project-one", "fingerprint-current")

    history_item = actions.list_history(owner, retry_context=retry_context)[0]
    recovery_item = actions.list_recovery(owner)[0]
    assert history_item.available_actions.retry
    assert history_item.available_actions.open_result
    assert history_item.available_actions.open_log
    assert recovery_item.available_actions.recover

    retry_result = actions.execute(history_item, TaskCenterAction.RETRY, owner, retry_context=retry_context)
    navigation_result = actions.execute(history_item, TaskCenterAction.OPEN_LOG, owner, retry_context=retry_context)
    recovery_result = actions.execute(recovery_item, TaskCenterAction.RECOVER, owner, retry_context=retry_context)
    assert retry_result.job_ref == JobRef("job-new", owner.owner_id, "run-new")
    assert navigation_result.navigation == TaskNavigationIntent("task.open_log")
    assert recovery_result.job_ref == JobRef("job-recovered", owner.owner_id, "run-old")

    with pytest.raises(TaskCenterActionError, match="历史记录已变化"):
        actions.execute(
            replace(history_item, revision=history_item.revision + 1),
            TaskCenterAction.RETRY,
            owner,
            retry_context=retry_context,
        )
