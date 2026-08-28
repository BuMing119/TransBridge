from __future__ import annotations

from datetime import UTC, datetime, timedelta

from transbridge.application.tasks import CallbackThreadBackend, JobState, OwnerRef, TaskRuntime
from transbridge.application.terminology.runtime import (
    TerminologyBusinessGuardResult,
    TerminologyTaskEntrypoint,
    TerminologyWorkloadExecution,
    TerminologyWorkloadRegistry,
)
from transbridge.application.terminology.workloads import (
    BuildFreshness,
    BuildWorkloadRequest,
    TerminologyExpectedState,
    TerminologyPhase,
    TerminologyProgress,
    TerminologyWorkloadResult,
    TerminologyWorkloadType,
)


class _Ids:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"terminology-run-{self.value}"


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 28, tzinfo=UTC)

    def now(self) -> datetime:
        self.value += timedelta(microseconds=1)
        return self.value


class _Monotonic:
    def __init__(self) -> None:
        self.value = 10.0

    def __call__(self) -> float:
        return self.value


class _CommitPort:
    def __init__(self, *, current: bool = True) -> None:
        self.current = current
        self.expected = None
        self.mutation_calls = 0

    def commit_if_current(self, expected, mutation):
        self.expected = expected
        if not self.current:
            return TerminologyBusinessGuardResult(False, "TERMINOLOGY_EXPECTED_STATE_STALE")
        mutation()
        self.mutation_calls += 1
        return TerminologyBusinessGuardResult(True)


def _owner() -> OwnerRef:
    return OwnerRef("operator", "gui", project_id="project-1", variant_id="variant-1")


def _request() -> BuildWorkloadRequest:
    return BuildWorkloadRequest(
        project_id="project-1",
        variant_id="variant-1",
        expected=TerminologyExpectedState(
            project_revision=1,
            variant_revision=2,
            source_graph_digest="graph",
            source_fingerprint_digest="sources",
            effective_version_id="effective",
            base_version_id="base",
            draft_id="draft",
            draft_revision=3,
            build_freshness_digest="freshness",
        ),
        build_key="build-key",
    )


def _runtime() -> TaskRuntime:
    return TaskRuntime(
        id_generator=_Ids(),
        clock=_Clock(),
        backend=CallbackThreadBackend(lambda _run_id, target: target()),
    )


def test_heartbeat_and_final_progress_precede_formal_commit_permit():
    runtime = _runtime()
    monotonic = _Monotonic()
    commit_port = _CommitPort()
    registry = TerminologyWorkloadRegistry()
    progress_events = []
    runtime.subscribe(lambda event: progress_events.append(event) if event.event_type.value == "progress" else None)

    def runner(request, context):
        context.progress.update(
            TerminologyProgress(
                phase=TerminologyPhase.EXTRACT,
                completed=1,
                total=4,
                current_object="source-a",
            )
        )
        monotonic.value += 2.0
        assert context.heartbeat()
        return TerminologyWorkloadExecution(
            TerminologyWorkloadResult(request.workload_type, output_ref="build:1"),
            lambda: None,
        )

    registry.bind(TerminologyWorkloadType.BUILD, runner)
    assert registry.workload_types == (TerminologyWorkloadType.BUILD,)
    tasks = TerminologyTaskEntrypoint(runtime, registry, commit_port, monotonic_clock=monotonic)
    deferred = tasks.submit(_request(), _owner())

    snapshot = runtime.get(deferred.ref, _owner())
    assert snapshot.state is JobState.COMPLETED
    assert dict(snapshot.progress)["phase"] == TerminologyPhase.FINALIZE.value
    assert any(dict(event.snapshot.progress).get("heartbeat_sequence") == 1 for event in progress_events)
    assert commit_port.mutation_calls == 1
    assert commit_port.expected == _request().expected
    assert tasks.result(deferred.ref, _owner()).committed


def test_cancel_immediately_blocks_refill_and_quarantines_late_result():
    runtime = _runtime()
    registry = TerminologyWorkloadRegistry()
    commit_port = _CommitPort()
    observed = {}

    def runner(request, context):
        runtime.cancel(context.lease.ref, context.lease.owner)
        observed["can_refill"] = context.can_refill()
        return TerminologyWorkloadExecution(
            TerminologyWorkloadResult(request.workload_type, output_ref="late-build"),
            lambda: observed.update(mutated=True),
        )

    registry.bind(TerminologyWorkloadType.BUILD, runner)
    tasks = TerminologyTaskEntrypoint(runtime, registry, commit_port)
    deferred = tasks.submit(_request(), _owner())

    assert runtime.get(deferred.ref, _owner()).state is JobState.CANCELLED
    assert observed == {"can_refill": False}
    assert commit_port.mutation_calls == 0
    assert tasks.result(deferred.ref, _owner()) is None


def test_business_expected_guard_marks_result_stale_without_changing_runtime_terminal_state():
    runtime = _runtime()
    registry = TerminologyWorkloadRegistry()
    commit_port = _CommitPort(current=False)
    mutated = []

    def runner(request, context):
        context.checkpoint()
        return TerminologyWorkloadExecution(
            TerminologyWorkloadResult(request.workload_type, output_ref="staged-build"),
            lambda: mutated.append(True),
        )

    registry.bind(TerminologyWorkloadType.BUILD, runner)
    tasks = TerminologyTaskEntrypoint(runtime, registry, commit_port)
    deferred = tasks.submit(_request(), _owner())

    result = tasks.result(deferred.ref, _owner())
    assert runtime.get(deferred.ref, _owner()).state is JobState.COMPLETED
    assert result is not None
    assert result.freshness is BuildFreshness.STALE
    assert not result.committed
    assert result.diagnostics == ("TERMINOLOGY_EXPECTED_STATE_STALE",)
    assert mutated == []


def test_owner_scope_must_match_request_project_and_variant():
    runtime = _runtime()
    tasks = TerminologyTaskEntrypoint(runtime, TerminologyWorkloadRegistry(), _CommitPort())
    wrong_owner = OwnerRef("operator", "gui", project_id="project-1", variant_id="other")

    try:
        tasks.submit(_request(), wrong_owner)
    except ValueError as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("mismatched terminology owner was accepted")
