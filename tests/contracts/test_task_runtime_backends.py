from __future__ import annotations

from datetime import UTC, datetime
import threading
import time

import pytest

from transbridge.application.tasks import (
    BoundedThreadPoolBackend,
    CallbackThreadBackend,
    CommitPermit,
    JobCapabilities,
    JobEventType,
    JobSpec,
    JobState,
    OwnerRef,
    ShutdownPolicy,
    StopPolicy,
    TaskRuntime,
    ThreadBackend,
)


class Ids:
    def __init__(self) -> None:
        self._value = 0

    def new_id(self) -> str:
        self._value += 1
        return f"run-{self._value}"


class Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@pytest.fixture
def owner() -> OwnerRef:
    return OwnerRef("owner", "test", session_id="session")


def spec(*, pause: bool = True, checkpoint: bool = False) -> JobSpec:
    return JobSpec(
        "translation",
        "variant:1",
        "sha256:1",
        capabilities=JobCapabilities(
            supports_pause=pause,
            supports_resume=pause,
            supports_cancel=True,
            supports_checkpoint=checkpoint,
        ),
    )


def runtime(backend=None) -> TaskRuntime:
    return TaskRuntime(id_generator=Ids(), clock=Clock(), backend=backend)


def test_commit_permit_is_one_shot_and_cancel_rejects_late_publish(owner):
    value = runtime()
    events = []
    value.subscribe(events.append)
    ref = value.submit(spec(), owner).ref
    value.start(ref, owner)
    permit = value.commit_permit(ref, owner)
    committed = []

    assert value.try_commit(permit, lambda: committed.append("first")).accepted
    stale = value.try_commit(permit, lambda: committed.append("stale"))
    assert not stale.accepted
    assert stale.reason == "revision_conflict"

    late_permit = value.commit_permit(ref, owner)
    value.cancel(ref, owner)
    late = value.try_commit(late_permit, lambda: committed.append("late"))
    assert not late.accepted
    assert committed == ["first"]
    assert events[-1].event_type is JobEventType.IGNORED
    assert events[-1].code == "ignored_commit_terminal_or_inactive"


def test_forged_permit_is_rejected_and_full_owner_scope_is_bound(owner):
    value = runtime()
    ref = value.submit(spec(), owner).ref
    value.start(ref, owner)
    real = value.commit_permit(ref, owner)
    forged_owner = OwnerRef(owner.owner_id, "other-entrypoint", session_id="other-session")
    forged = CommitPermit(real.run_id, forged_owner, real.revision, "invented")
    committed = []

    result = value.try_commit(forged, lambda: committed.append("forged"))
    assert not result.accepted
    assert result.reason == "owner_mismatch"
    assert committed == []
    assert value.try_commit(real, lambda: committed.append("real")).accepted


def test_commit_mutation_exception_consumes_permit_and_fails_closed(owner):
    value = runtime()
    events = []
    value.subscribe(events.append)
    ref = value.submit(spec(), owner).ref
    value.start(ref, owner)
    permit = value.commit_permit(ref, owner)

    with pytest.raises(OSError, match="atomic publish failed"):
        value.try_commit(permit, lambda: (_ for _ in ()).throw(OSError("atomic publish failed")))

    assert value.get(ref, owner).state is JobState.FAILED
    assert events[-1].code == "commit_mutation_failed"
    replay = value.try_commit(permit, lambda: None)
    assert not replay.accepted


def test_paused_cancel_sets_token_and_preserves_terminal_exclusivity(owner):
    value = runtime()
    ref = value.submit(spec(), owner).ref
    value.start(ref, owner)
    value.pause(ref, owner)

    cancelling = value.cancel(ref, owner)
    assert cancelling.state is JobState.CANCELLING
    assert value.cancellation_token(ref, owner).is_cancelled
    assert value.finish_cancelled(ref, owner).state is JobState.CANCELLED


def test_cancelling_event_never_exposes_an_unset_token(owner):
    value = runtime()
    observed = []
    ref = value.submit(spec(), owner).ref

    def subscriber(event):
        if event.snapshot.state is JobState.CANCELLING:
            observed.append(value.cancellation_token(ref, owner).is_cancelled)

    value.subscribe(subscriber)
    value.start(ref, owner)
    value.cancel(ref, owner)
    assert observed == [True]


def test_stop_policy_requests_checkpoint_only_when_capable(owner):
    value = runtime()
    capable = value.submit(spec(checkpoint=True), owner).ref
    value.start(capable, owner)
    result = value.stop(capable, owner, policy=StopPolicy.PRESERVE_CHECKPOINT)
    assert result.snapshot.state is JobState.CANCELLING
    assert result.checkpoint_requested
    assert value.cancellation_token(capable, owner).reason.startswith("stop requested")

    incapable = value.submit(spec(checkpoint=False), owner).ref
    value.start(incapable, owner)
    result = value.stop(incapable, owner, policy=StopPolicy.PRESERVE_CHECKPOINT)
    assert not result.checkpoint_requested


def test_control_projection_hides_unsupported_pause(owner):
    value = runtime()
    ref = value.submit(spec(pause=False), owner).ref
    queued = value.controls(ref, owner)
    assert not queued.pause_visible
    assert not queued.resume_visible
    assert queued.cancel_enabled

    value.start(ref, owner)
    running = value.controls(ref, owner)
    assert running.cancel_enabled and running.stop_enabled
    value.complete(ref, owner)
    assert not value.controls(ref, owner).cancel_enabled


@pytest.mark.parametrize("backend", [ThreadBackend(), BoundedThreadPoolBackend(max_workers=2)])
def test_backend_completion_is_committed_by_runtime(owner, backend):
    value = runtime(backend)
    ref = value.submit(spec(), owner).ref
    value.schedule(ref, owner, lambda token: token.raise_if_cancelled())

    assert backend.join(ref.run_id, 2)
    assert value.get(ref, owner).state is JobState.COMPLETED
    assert backend.close(1)


def test_workload_exception_becomes_failed_with_diagnostic(owner):
    backend = ThreadBackend()
    value = runtime(backend)
    events = []
    value.subscribe(events.append)
    ref = value.submit(spec(), owner).ref

    def fail(_token):
        raise LookupError("backend exploded")

    value.schedule(ref, owner, fail)
    assert backend.join(ref.run_id, 2)
    assert value.get(ref, owner).state is JobState.FAILED
    assert any(event.code == "backend_workload_failed" for event in events)


def test_cancel_completion_race_never_commits_completed_after_cancel(owner):
    backend = ThreadBackend()
    value = runtime(backend)
    entered = threading.Event()
    release = threading.Event()
    side_effects = []
    ref = value.submit(spec(), owner).ref

    def workload(token):
        entered.set()
        release.wait(2)
        token.raise_if_cancelled()
        side_effects.append("late")

    value.schedule(ref, owner, workload)
    assert entered.wait(1)
    value.cancel(ref, owner)
    release.set()
    assert backend.join(ref.run_id, 2)
    assert value.get(ref, owner).state is JobState.CANCELLED
    assert side_effects == []


def test_shutdown_closes_admission_and_does_not_claim_timed_out_thread_released(owner):
    backend = ThreadBackend()
    value = runtime(backend)
    release = threading.Event()
    ref = value.submit(spec(), owner).ref
    value.schedule(ref, owner, lambda _token: release.wait(2))

    result = value.shutdown(grace=0.01, policy=ShutdownPolicy.CANCEL)
    assert ref in result.timed_out
    assert not result.backend_released
    assert value.get(ref, owner).state is JobState.CANCELLING
    with pytest.raises(RuntimeError, match="closed"):
        value.submit(spec(), owner)
    release.set()
    assert backend.join(ref.run_id, 2)


def test_shutdown_wait_drains_without_issuing_cancellation(owner):
    backend = ThreadBackend()
    value = runtime(backend)
    ref = value.submit(spec(), owner).ref
    value.schedule(ref, owner, lambda token: time.sleep(0.01))

    result = value.shutdown(grace=2, policy=ShutdownPolicy.WAIT)
    assert result.backend_released
    assert ref in result.joined
    assert not value.cancellation_token(ref, owner).is_cancelled
    assert value.get(ref, owner).state is JobState.COMPLETED


def test_shutdown_wait_uses_grace_for_manually_driven_job(owner):
    value = runtime()
    ref = value.submit(spec(), owner).ref
    value.start(ref, owner)

    finisher = threading.Thread(target=lambda: (time.sleep(0.03), value.complete(ref, owner)))
    finisher.start()
    started = time.monotonic()
    result = value.shutdown(grace=1, policy=ShutdownPolicy.WAIT)
    elapsed = time.monotonic() - started
    finisher.join(1)

    assert elapsed >= 0.02
    assert ref in result.joined
    assert ref not in result.timed_out


def test_shutdown_wait_times_out_manual_job_only_after_grace(owner):
    value = runtime()
    ref = value.submit(spec(), owner).ref
    value.start(ref, owner)
    started = time.monotonic()
    result = value.shutdown(grace=0.03, policy=ShutdownPolicy.WAIT)
    elapsed = time.monotonic() - started
    assert elapsed >= 0.02
    assert ref in result.timed_out
    assert not result.backend_released


def test_bounded_pool_never_exceeds_three_active_workloads(owner):
    backend = BoundedThreadPoolBackend(max_workers=3)
    value = runtime(backend)
    lock = threading.Lock()
    active = 0
    maximum = 0
    refs = []

    def workload(_token):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.003)
        with lock:
            active -= 1

    for _ in range(100):
        ref = value.submit(spec(), owner).ref
        refs.append(ref)
        value.schedule(ref, owner, workload)

    result = value.shutdown(grace=5, policy=ShutdownPolicy.WAIT)
    assert result.backend_released
    assert maximum <= 3
    assert all(value.get(ref, owner).state is JobState.COMPLETED for ref in refs)


def test_fake_llm_cancellation_stops_new_side_effects_within_budget(owner):
    backend = BoundedThreadPoolBackend(max_workers=3)
    value = runtime(backend)
    lock = threading.Lock()
    refs = []
    started = set()
    first_started = threading.Event()
    stopped_at = {}
    cancel_at = 0.0

    def workload(token):
        run_id = threading.current_thread().name
        with lock:
            started.add(run_id)
            first_started.set()
        try:
            while not token.wait(0.002):
                # Represents the safe point before starting the next external request.
                token.raise_if_cancelled()
        finally:
            with lock:
                stopped_at[run_id] = time.monotonic()

    for _ in range(100):
        ref = value.submit(spec(), owner).ref
        refs.append(ref)
        value.schedule(ref, owner, workload)

    assert first_started.wait(5), "backend did not start a cancellation probe workload"
    deadline = time.monotonic() + 1
    while len(started) < 3 and time.monotonic() < deadline:
        time.sleep(0.002)
    cancel_at = time.monotonic()
    for ref in refs:
        value.cancel(ref, owner)

    result = value.shutdown(grace=2, policy=ShutdownPolicy.CANCEL)
    assert result.backend_released
    latencies = sorted(stopped - cancel_at for stopped in stopped_at.values())
    assert latencies
    p95 = latencies[max(0, int(len(latencies) * 0.95) - 1)]
    assert p95 < 1.0


def test_callback_backend_is_a_framework_neutral_signal_wrapper():
    callbacks = {}
    cancelled = []
    backend = CallbackThreadBackend(
        dispatch=lambda run_id, target: callbacks.setdefault(run_id, target),
        cancel=cancelled.append,
        wait=lambda run_id, timeout: run_id in callbacks,
    )
    backend.start("run", lambda: None)
    backend.cancel_hint("run")
    assert cancelled == ["run"]
    assert backend.join("run", 0)


def test_backend_start_failure_is_not_reported_as_completed(owner):
    class BrokenBackend:
        def start(self, run_id, target):
            raise OSError("cannot schedule")

        def cancel_hint(self, run_id):
            pass

        def join(self, run_id, timeout=None):
            return True

        def close(self, timeout=None):
            return True

    value = runtime(BrokenBackend())
    ref = value.submit(spec(), owner).ref
    with pytest.raises(OSError, match="cannot schedule"):
        value.schedule(ref, owner, lambda _token: None)
    assert value.get(ref, owner).state is JobState.FAILED


def test_shutdown_contains_backend_join_exception_and_marks_release_incomplete(owner):
    class BrokenJoinBackend:
        def start(self, run_id, target):
            pass

        def cancel_hint(self, run_id):
            pass

        def join(self, run_id, timeout=None):
            raise OSError("join failed")

        def close(self, timeout=None):
            return False

    value = runtime(BrokenJoinBackend())
    events = []
    value.subscribe(events.append)
    ref = value.submit(spec(), owner).ref
    value.schedule(ref, owner, lambda _token: None)

    result = value.shutdown(grace=0, policy=ShutdownPolicy.CANCEL)
    assert ref in result.timed_out
    assert not result.backend_released
    assert any(event.code == "backend_join_failed" for event in events)
