from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json

import pytest

from transbridge.application.contracts import JobRef
from transbridge.application.tasks import (
    CheckpointCatalogItem,
    CheckpointExpectation,
    CheckpointRecord,
    FilesystemTaskHistoryPort,
    InMemoryTaskHistoryPort,
    JobCapabilities,
    JobEventType,
    JobSpec,
    OwnerRef,
    RecoveryCatalog,
    RecoveryExpectationRegistry,
    TaskActivityEvidence,
    TaskHistoryError,
    TaskHistoryRecord,
    TaskHistoryRecorder,
    TaskRecoveryDescriptor,
    TaskRetryContext,
    TaskRetryError,
    TaskRetryIntentRegistry,
    TaskRuntime,
    activity_from_snapshot,
)


class SequenceIds:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"activity-run-{self.value}"


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 24, tzinfo=UTC)

    def now(self) -> datetime:
        self.value += timedelta(microseconds=1)
        return self.value


def owner(*, session_id: str = "session-1", manage: bool = False) -> OwnerRef:
    return OwnerRef(
        owner_id="owner-1" if not manage else "task-manager",
        entrypoint="gui",
        project_id=None if manage else "project-1",
        session_id=None if manage else session_id,
        permissions=frozenset({TaskRuntime.MANAGE_PERMISSION}) if manage else frozenset(),
    )


def spec(*, checkpoint: bool = True) -> JobSpec:
    return JobSpec(
        job_type="translation",
        input_ref="secret://must-not-enter-history",
        input_fingerprint="sha256:input",
        display_name="AI translation",
        config_digest="secret-config-digest",
        capabilities=JobCapabilities(
            supports_pause=True,
            supports_resume=True,
            supports_cancel=True,
            supports_checkpoint=checkpoint,
        ),
        metadata=(("prompt", "secret prompt"),),
    )


def terminal_record(runtime: TaskRuntime, actor: OwnerRef) -> TaskHistoryRecord:
    events = []
    runtime.subscribe(events.append)
    ref = runtime.submit(spec(), actor).ref
    runtime.start(ref, actor)
    runtime.fail(ref, actor)
    event = next(event for event in events if event.event_type is JobEventType.FINISHED)
    return TaskHistoryRecord.from_terminal_event(event)


def test_activity_actions_require_snapshot_capability_and_external_evidence() -> None:
    runtime = TaskRuntime(id_generator=SequenceIds(), clock=Clock())
    actor = owner()
    ref = runtime.submit(spec(), actor).ref
    runtime.start(ref, actor)

    unsupported = activity_from_snapshot(runtime.get(ref, actor))
    assert unsupported.available_actions.pause is True
    assert unsupported.available_actions.recover is False
    assert unsupported.available_actions.retry is False
    assert unsupported.available_actions.open_result is False

    evidenced = activity_from_snapshot(
        runtime.get(ref, actor),
        evidence=TaskActivityEvidence(
            recoverable=True,
            recoverability_reason="recoverable",
            retryable=True,
            result_available=True,
            log_available=True,
        ),
    )
    assert evidenced.available_actions.recover is True
    assert evidenced.available_actions.retry is True
    assert evidenced.available_actions.open_result is True
    assert evidenced.available_actions.open_log is True


def test_history_is_terminal_immutable_owner_scoped_and_bounded(tmp_path) -> None:
    runtime = TaskRuntime(id_generator=SequenceIds(), clock=Clock())
    actor = owner()
    record = terminal_record(runtime, actor)
    history = FilesystemTaskHistoryPort(tmp_path, max_records=1)

    history.append(record)
    history.append(record)  # exact duplicate is idempotent
    assert history.list(actor) == (record,)
    assert history.list(owner(session_id="other")) == ()
    assert history.list(owner(manage=True)) == (record,)

    raw = (tmp_path / "task-history-v1.json").read_text(encoding="utf-8")
    assert "secret://" not in raw
    assert "secret prompt" not in raw
    assert "secret-config-digest" not in raw
    assert json.loads(raw)["schema_version"] == 1

    changed = replace(record, terminal_sequence=record.terminal_sequence + 1)
    with pytest.raises(TaskHistoryError) as captured:
        history.append(changed)
    assert captured.value.code == "history_immutable_conflict"


def test_in_memory_history_retention_drops_oldest_terminal_record() -> None:
    history = InMemoryTaskHistoryPort(max_records=1)
    first_runtime = TaskRuntime(id_generator=SequenceIds(), clock=Clock())
    first = terminal_record(first_runtime, owner())
    second_runtime = TaskRuntime(id_generator=SequenceIds(), clock=Clock())
    second = terminal_record(second_runtime, owner())
    second = TaskHistoryRecord(
        run_id="activity-run-new",
        job_id="activity-run-new",
        owner=second.owner,
        job_type=second.job_type,
        display_name=second.display_name,
        state=second.state,
        terminal_revision=second.terminal_revision,
        terminal_sequence=second.terminal_sequence,
        created_at=second.created_at,
        finished_at=second.finished_at,
    )

    history.append(first)
    history.append(second)
    assert history.list(owner()) == (second,)


def test_history_recorder_failure_does_not_change_runtime_terminal_state() -> None:
    class BrokenHistory:
        def append(self, record):
            del record
            raise TaskHistoryError("history_write_failed", "disk full")

        def list(self, actor, *, limit=None):
            del actor, limit
            return ()

    runtime = TaskRuntime(id_generator=SequenceIds(), clock=Clock())
    failures = []
    recorder = TaskHistoryRecorder(runtime, BrokenHistory(), on_failure=failures.append)
    recorder.start()
    actor = owner()
    ref = runtime.submit(spec(), actor).ref
    runtime.start(ref, actor)
    final = runtime.complete(ref, actor)

    assert final.state.value == "completed"
    assert failures[0].code == "history_write_failed"
    recorder.close()


class Catalog:
    def __init__(self, *items: CheckpointCatalogItem) -> None:
        self.items = items

    def list_candidates(self):
        return self.items


def checkpoint(actor: OwnerRef) -> CheckpointRecord:
    return CheckpointRecord(
        run_id="old-run",
        owner=actor,
        spec_fingerprint="sha256:spec",
        input_fingerprint="sha256:input",
        revision=3,
    )


def test_recovery_catalog_requires_registered_current_identity_and_owner() -> None:
    actor = owner()
    record = checkpoint(actor)
    item = CheckpointCatalogItem("storage-1", record=record)
    registry = RecoveryExpectationRegistry()
    catalog = RecoveryCatalog(Catalog(item), registry)

    unavailable = catalog.list(actor)
    assert unavailable[0].recoverable is False
    assert unavailable[0].reason_code == "recovery_intent_unregistered"
    assert catalog.list(owner(session_id="other")) == ()

    registry.register(
        TaskRecoveryDescriptor(
            job_type="translation",
            display_name="Resume translation",
            expectation=CheckpointExpectation(
                run_id=record.run_id,
                owner=record.owner,
                spec_fingerprint="wrong-spec",
                input_fingerprint=record.input_fingerprint,
            ),
        )
    )
    mismatch = catalog.list(actor)[0]
    assert mismatch.recoverable is False
    assert mismatch.reason_code == "checkpoint_identity_mismatch"

    registry.unregister(record.run_id)
    registry.register(
        TaskRecoveryDescriptor(
            job_type="translation",
            display_name="Resume translation",
            expectation=CheckpointExpectation(
                run_id=record.run_id,
                owner=record.owner,
                spec_fingerprint=record.spec_fingerprint,
                input_fingerprint=record.input_fingerprint,
            ),
        )
    )
    assert catalog.list(actor)[0].recoverable is True


def test_recovery_catalog_exposes_corrupt_entries_to_manager_only() -> None:
    item = CheckpointCatalogItem("broken", error_code="checkpoint_invalid_json", error_message="bad")
    catalog = RecoveryCatalog(Catalog(item), RecoveryExpectationRegistry())

    assert catalog.list(owner()) == ()
    managed = catalog.list(owner(manage=True))
    assert managed[0].reason_code == "checkpoint_invalid_json"
    assert managed[0].recoverable is False


def test_retry_registry_repreflights_through_handler_and_requires_new_run_id() -> None:
    runtime = TaskRuntime(id_generator=SequenceIds(), clock=Clock())
    actor = owner()
    previous = terminal_record(runtime, actor)
    registry = TaskRetryIntentRegistry()
    calls = []

    def retry_intent(record, context):
        calls.append((record.run_id, context.context_fingerprint))
        return runtime.submit(spec(), actor).ref

    registry.register("translation", retry_intent)
    new_ref = registry.retry(previous, TaskRetryContext(actor, "variant:current", "sha256:current"))

    assert new_ref.run_id != previous.run_id
    assert calls == [(previous.run_id, "sha256:current")]

    registry.unregister("translation", retry_intent)
    registry.register("translation", lambda _record, _context: JobRef(previous.job_id, actor.owner_id, previous.run_id))
    with pytest.raises(TaskRetryError) as captured:
        registry.retry(previous, TaskRetryContext(actor, "variant:current", "sha256:current"))
    assert captured.value.code == "retry_reused_run_id"
