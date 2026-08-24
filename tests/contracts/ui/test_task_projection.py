from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from transbridge.application.tasks import (
    JobCapabilities,
    JobEvent,
    JobEventType,
    JobSpec,
    JobState,
    OwnerRef,
    TaskRuntime,
)
from transbridge.ui.presentation import TaskProjectionBinding, TaskProjectionReducer


class SequenceIds:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"ui-task-{self.value}"


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 24, tzinfo=UTC)

    def now(self) -> datetime:
        self.value += timedelta(microseconds=1)
        return self.value


class CountingRuntime(TaskRuntime):
    def __init__(self) -> None:
        super().__init__(id_generator=SequenceIds(), clock=Clock())
        self.list_calls = 0

    def list(self, actor):
        self.list_calls += 1
        return super().list(actor)


def owner(*, session: str = "session-1", manager: bool = False) -> OwnerRef:
    return OwnerRef(
        owner_id="owner" if not manager else "manager",
        entrypoint="gui",
        project_id=None if manager else "project",
        session_id=None if manager else session,
        permissions=frozenset({TaskRuntime.MANAGE_PERMISSION}) if manager else frozenset(),
    )


def spec() -> JobSpec:
    return JobSpec(
        job_type="translation",
        input_ref="variant:one",
        input_fingerprint="sha256:one",
        capabilities=JobCapabilities(
            supports_pause=True,
            supports_resume=True,
            supports_cancel=True,
            supports_checkpoint=True,
        ),
    )


def test_reducer_rejects_duplicate_out_of_order_and_terminal_reversal() -> None:
    runtime = CountingRuntime()
    actor = owner()
    events = []
    runtime.subscribe(events.append)
    ref = runtime.submit(spec(), actor).ref
    runtime.start(ref, actor)
    runtime.fail(ref, actor)
    created, running, failed = events
    reducer = TaskProjectionReducer()

    current = reducer.reduce(None, created)
    assert current.accepted is True
    current = reducer.reduce(current.state, running)
    assert current.accepted is True
    assert reducer.reduce(current.state, running).reason == "duplicate"
    assert reducer.reduce(current.state, created).reason == "out_of_order"
    terminal = reducer.reduce(current.state, failed)
    assert terminal.accepted is True

    false_running = replace(
        running.snapshot,
        state=JobState.RUNNING,
        sequence=failed.sequence + 1,
        revision=failed.revision + 1,
    )
    reversing_event = JobEvent(
        event_type=JobEventType.STATE_CHANGED,
        snapshot=false_running,
        sequence=false_running.sequence,
        revision=false_running.revision,
        occurred_at=false_running.updated_at,
    )
    rejected = reducer.reduce(terminal.state, reversing_event)
    assert rejected.accepted is False
    assert rejected.reason == "terminal_state"
    assert rejected.state.state is JobState.FAILED


def test_reducer_keeps_bounded_diagnostic_references_without_messages() -> None:
    runtime = CountingRuntime()
    actor = owner()
    events = []
    runtime.subscribe(events.append)
    runtime.submit(spec(), actor)
    created = events[0]
    reducer = TaskProjectionReducer(max_diagnostics=2)
    state = reducer.reduce(None, created).state

    for offset in range(1, 4):
        snapshot = replace(created.snapshot, sequence=created.sequence + offset)
        diagnostic = JobEvent(
            event_type=JobEventType.DIAGNOSTIC,
            snapshot=snapshot,
            sequence=snapshot.sequence,
            revision=snapshot.revision,
            occurred_at=snapshot.updated_at,
            code=f"diagnostic-{offset}",
            message="secret path and prompt are not projected",
        )
        state = reducer.reduce(state, diagnostic).state

    assert [item.code for item in state.diagnostic_refs] == ["diagnostic-2", "diagnostic-3"]
    assert all(not hasattr(item, "message") for item in state.diagnostic_refs)


def test_binding_filters_complete_owner_scope_and_manager_can_cross_scope() -> None:
    runtime = CountingRuntime()
    first = owner(session="one")
    second = owner(session="two")
    first_seen = []
    manager_seen = []
    first_binding = TaskProjectionBinding(runtime, first, first_seen.append)
    manager_binding = TaskProjectionBinding(runtime, owner(manager=True), manager_seen.append)
    first_binding.start()
    manager_binding.start()

    runtime.submit(spec(), first)
    runtime.submit(spec(), second)

    assert len(first_seen) == 1
    assert first_seen[0].owner.session_id == "one"
    assert {state.owner.session_id for state in manager_seen} == {"one", "two"}
    first_binding.close()
    manager_binding.close()


def test_one_hundred_binding_lifecycles_detach_and_never_poll() -> None:
    runtime = CountingRuntime()
    actor = owner()
    received = []

    for _ in range(100):
        binding = TaskProjectionBinding(runtime, actor, received.append)
        binding.start()
        binding.close()
        binding.close()

    assert runtime.list_calls == 100  # exactly one initial seed per lifecycle
    runtime.submit(spec(), actor)
    assert received == []


def test_binding_seeds_existing_state_once_then_streams_events() -> None:
    runtime = CountingRuntime()
    actor = owner()
    ref = runtime.submit(spec(), actor).ref
    runtime.start(ref, actor)
    seen = []
    binding = TaskProjectionBinding(runtime, actor, seen.append)

    binding.start()
    runtime.pause(ref, actor)
    binding.close()
    runtime.resume(ref, actor)

    assert [state.state for state in seen] == [JobState.RUNNING, JobState.PAUSED]
    assert runtime.list_calls == 1
