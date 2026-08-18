from __future__ import annotations

from datetime import UTC, datetime, timedelta
import subprocess
import sys
import threading

import pytest

from transbridge.application.contracts import Deferred, JobRef, OperationResult
from transbridge.application.tasks import (
    JobCapabilities,
    JobEventType,
    JobSpec,
    JobState,
    OwnerRef,
    TaskAccessError,
    TaskEventFilter,
    TaskRuntime,
    TransitionError,
)


class SequenceIds:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"unpredictable-run-{self.value}"


class AdvancingClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 18, tzinfo=UTC)

    def now(self) -> datetime:
        self.value += timedelta(microseconds=1)
        return self.value


@pytest.fixture
def runtime() -> TaskRuntime:
    return TaskRuntime(id_generator=SequenceIds(), clock=AdvancingClock())


@pytest.fixture
def owner() -> OwnerRef:
    return OwnerRef(
        owner_id="owner-1",
        entrypoint="gui",
        project_id="project-1",
        session_id="session-1",
    )


def specification(*, pause: bool = True, cancel: bool = True) -> JobSpec:
    return JobSpec(
        job_type="translation",
        input_ref="variant:one",
        input_fingerprint="sha256:abc",
        capabilities=JobCapabilities(
            supports_pause=pause,
            supports_resume=pause,
            supports_cancel=cancel,
        ),
    )


def submitted(runtime: TaskRuntime, owner: OwnerRef, **kwargs) -> JobRef:
    result = runtime.submit(specification(**kwargs), owner)
    assert isinstance(result, Deferred)
    assert isinstance(result.ref, JobRef)
    return result.ref


def test_submit_is_deferred_and_sync_result_remains_distinct(runtime, owner):
    ref = submitted(runtime, owner)
    assert runtime.get(ref, owner).state is JobState.QUEUED
    assert not isinstance(OperationResult.completed(value="done"), Deferred)


def test_legal_state_flow_and_monotonic_event_sequence(runtime, owner):
    events = []
    runtime.subscribe(events.append)
    ref = submitted(runtime, owner)
    runtime.start(ref, owner)
    runtime.pause(ref, owner)
    runtime.resume(ref, owner)
    final = runtime.complete(ref, owner)

    assert final.state is JobState.COMPLETED
    assert final.is_terminal
    assert [event.sequence for event in events] == [1, 2, 3, 4, 5]
    assert [event.revision for event in events] == [0, 1, 2, 3, 4]
    assert events[-1].event_type is JobEventType.FINISHED


@pytest.mark.parametrize("terminal", ["complete", "fail"])
def test_terminal_state_cannot_be_overwritten(runtime, owner, terminal):
    ref = submitted(runtime, owner)
    runtime.start(ref, owner)
    getattr(runtime, terminal)(ref, owner)

    with pytest.raises(TransitionError, match="cannot transition") as captured:
        runtime.finish_cancelled(ref, owner)
    assert captured.value.code == "terminal_state"


def test_cancel_flow_has_explicit_cancelling_state(runtime, owner):
    ref = submitted(runtime, owner)
    runtime.start(ref, owner)
    cancelling = runtime.cancel(ref, owner)
    assert cancelling.state is JobState.CANCELLING
    assert runtime.finish_cancelled(ref, owner).state is JobState.CANCELLED


def test_queued_cancel_is_terminal_without_start(runtime, owner):
    ref = submitted(runtime, owner)
    assert runtime.cancel(ref, owner).state is JobState.CANCELLED


def test_owner_scope_and_forged_reference_are_rejected(runtime, owner):
    ref = submitted(runtime, owner)
    other = OwnerRef(owner_id="owner-2", entrypoint="gui")
    with pytest.raises(TaskAccessError) as owner_error:
        runtime.get(ref, other)
    assert owner_error.value.code == "owner_mismatch"

    forged = JobRef(ref.job_id, "owner-2", ref.run_id)
    with pytest.raises(TaskAccessError) as ref_error:
        runtime.get(forged, owner)
    assert ref_error.value.code == "job_not_found"


def test_explicit_manager_permission_crosses_owner_scope(runtime, owner):
    ref = submitted(runtime, owner)
    manager = OwnerRef(
        owner_id="administrator",
        entrypoint="cli",
        permissions=frozenset({TaskRuntime.MANAGE_PERMISSION}),
    )
    assert runtime.start(ref, manager).state is JobState.RUNNING


@pytest.mark.parametrize(
    ("action", "kwargs"),
    [("pause", {"pause": False}), ("cancel", {"cancel": False})],
)
def test_controls_require_declared_capability(runtime, owner, action, kwargs):
    ref = submitted(runtime, owner, **kwargs)
    runtime.start(ref, owner)
    with pytest.raises(TransitionError) as captured:
        getattr(runtime, action)(ref, owner)
    assert captured.value.code == "unsupported_control"


def test_expected_revision_prevents_lost_update(runtime, owner):
    ref = submitted(runtime, owner)
    runtime.start(ref, owner, expected_revision=0)
    with pytest.raises(TransitionError) as captured:
        runtime.pause(ref, owner, expected_revision=0)
    assert captured.value.code == "revision_conflict"


def test_subscription_disposes_exact_wrapper_and_is_idempotent(runtime, owner):
    received = []
    subscription = runtime.subscribe(received.append)
    submitted(runtime, owner)
    subscription.close()
    subscription.close()
    submitted(runtime, owner)
    assert len(received) == 1
    assert subscription.closed


def test_subscription_can_close_itself_inside_callback(runtime, owner):
    received = []
    holder = {}

    def callback(event):
        received.append(event)
        holder["subscription"].close()

    holder["subscription"] = runtime.subscribe(callback)
    ref = submitted(runtime, owner)
    runtime.start(ref, owner)
    assert len(received) == 1


def test_callback_failure_does_not_change_terminal_state(runtime, owner):
    runtime.subscribe(lambda _event: (_ for _ in ()).throw(RuntimeError("boom")))
    ref = submitted(runtime, owner)
    runtime.start(ref, owner)
    assert runtime.complete(ref, owner).state is JobState.COMPLETED


def test_event_filter_is_owner_run_and_type_scoped(runtime, owner):
    ref = submitted(runtime, owner)
    received = []
    runtime.subscribe(
        received.append,
        event_filter=TaskEventFilter(
            run_id=ref.run_id,
            owner_id=owner.owner_id,
            event_types=frozenset({JobEventType.FINISHED}),
        ),
    )
    runtime.start(ref, owner)
    runtime.complete(ref, owner)
    assert [event.event_type for event in received] == [JobEventType.FINISHED]


def test_concurrent_terminal_race_commits_exactly_one_terminal(runtime, owner):
    ref = submitted(runtime, owner)
    runtime.start(ref, owner)
    barrier = threading.Barrier(3)
    outcomes = []
    lock = threading.Lock()

    def finish(action):
        barrier.wait()
        try:
            state = action(ref, owner).state
            result = ("ok", state)
        except TransitionError as exc:
            result = ("error", exc.code)
        with lock:
            outcomes.append(result)

    threads = [
        threading.Thread(target=finish, args=(runtime.complete,)),
        threading.Thread(target=finish, args=(runtime.fail,)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert [kind for kind, _ in outcomes].count("ok") == 1
    assert [kind for kind, _ in outcomes].count("error") == 1
    assert runtime.get(ref, owner).state in {JobState.COMPLETED, JobState.FAILED}


def test_optimized_mode_does_not_change_validation_contract():
    code = """
from datetime import UTC, datetime
from transbridge.application.tasks import JobSpec, OwnerRef, TaskRuntime, TransitionError
class Ids:
    def new_id(self): return 'optimized-run'
class Clock:
    def now(self): return datetime(2026, 8, 18, tzinfo=UTC)
runtime = TaskRuntime(id_generator=Ids(), clock=Clock())
owner = OwnerRef('owner', 'cli')
ref = runtime.submit(JobSpec('test', 'input', 'hash'), owner).ref
runtime.start(ref, owner)
runtime.complete(ref, owner)
try:
    runtime.fail(ref, owner)
except TransitionError as exc:
    print(exc.code)
"""
    result = subprocess.run(
        [sys.executable, "-O", "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "terminal_state"
