from __future__ import annotations

from dataclasses import replace

import pytest

from transbridge.application.contracts import JobRef, OperationOutcome, RequestContext
from transbridge.application.sessions import (
    ActiveSession,
    ControllerSnapshot,
    SessionAggregate,
    SessionEventKind,
    SessionJobRef,
    SessionLifecycleService,
    SessionRuntimeEvent,
    SessionSnapshot,
)
from transbridge.application.tasks.models import JobState, OwnerRef
from transbridge.persistence.v2 import ProjectId, SessionId, SessionRef, VariantId


def _snapshot(session_id: str, *, revision: int = 2) -> SessionSnapshot:
    ref = SessionRef(SessionId(session_id))
    owner = OwnerRef("owner", "gui", session_id=session_id)
    return SessionSnapshot(
        ref=ref,
        name=session_id,
        owner=owner,
        messages=({"role": "user", "content": session_id},),
        backend_history=({"role": "user", "content": f"backend-{session_id}"},),
        backend_summary=None,
        controller=ControllerSnapshot(),
        project_id=None,
        variant_id=None,
        approvals=(),
        jobs=(SessionJobRef(JobRef(f"job-{session_id}", owner.owner_id, f"run-{session_id}"), JobState.RUNNING),),
        revision=revision,
        created_at="2026-08-18T00:00:00Z",
        last_active_at="2026-08-18T00:01:00Z",
    )


class _Repository:
    def __init__(self, snapshots: list[SessionSnapshot]) -> None:
        self.snapshots = {item.ref.identity.value: item for item in snapshots}
        self.load_calls: list[str] = []
        self.save_calls: list[tuple[str, int]] = []
        self.fail_save = False

    def load(self, ref, context):
        self.load_calls.append(ref.identity.value)
        return self.snapshots[ref.identity.value]

    def save(self, snapshot, *, expected_revision, context):
        self.save_calls.append((snapshot.ref.identity.value, expected_revision))
        if self.fail_save:
            raise OSError("private path must not escape")
        self.snapshots[snapshot.ref.identity.value] = snapshot
        return snapshot


class _Unit:
    def __init__(self, factory) -> None:
        self.factory = factory
        self.staged = None
        self.rolled_back = False

    def stage_activate(self, old, candidate):
        self.staged = (old, candidate)

    def commit(self):
        if self.factory.fail_commit:
            raise OSError("commit failed")
        self.factory.commits.append(self.staged)

    def rollback(self):
        self.rolled_back = True
        self.factory.rollbacks += 1


class _UnitFactory:
    def __init__(self) -> None:
        self.fail_commit = False
        self.commits = []
        self.rollbacks = 0

    def begin(self):
        return _Unit(self)


def _context(session_id: str | None, *, owner: str = "owner") -> RequestContext:
    return RequestContext(owner, run_id="switch-run", session_id=session_id)


def _dirty(aggregate: SessionAggregate) -> None:
    snapshot = aggregate.snapshot()
    aggregate.replace_snapshot(
        replace(snapshot, backend_summary="dirty"),
        expected_revision=snapshot.revision,
    )


def test_prepare_switch_saves_dirty_current_before_loading_target() -> None:
    current = SessionAggregate(_snapshot("session-a"))
    _dirty(current)
    repository = _Repository([_snapshot("session-a"), _snapshot("session-b")])
    repository.fail_save = True
    service = SessionLifecycleService(
        repository,
        _UnitFactory(),
        active=ActiveSession(current, 2),
    )

    result = service.prepare_switch(SessionRef(SessionId("session-b")), _context("session-b"))

    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "SESSION_SAVE_FAILED"
    assert repository.load_calls == []
    assert service.active is not None and service.active.aggregate is current


def test_commit_failure_keeps_old_active_and_does_not_render_candidate() -> None:
    current = SessionAggregate(_snapshot("session-a"))
    units = _UnitFactory()
    units.fail_commit = True
    projections: list[str | None] = []
    service = SessionLifecycleService(
        _Repository([_snapshot("session-a"), _snapshot("session-b")]),
        units,
        active=ActiveSession(current, 2),
        projection=lambda item: projections.append(None if item is None else item.ref.identity.value),
        token_factory=lambda: "token",
    )
    prepared = service.prepare_switch(SessionRef(SessionId("session-b")), _context("session-b"))

    result = service.commit_switch(prepared.value["token"], _context("session-b"))

    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "SESSION_COMMIT_FAILED"
    assert service.active is not None and service.active.aggregate is current
    assert projections == []
    assert units.rollbacks == 1


def test_prepared_transition_is_owner_bound_single_use_and_stale_safe() -> None:
    service = SessionLifecycleService(
        _Repository([_snapshot("session-b")]),
        _UnitFactory(),
        token_factory=lambda: "token",
    )
    prepared = service.prepare_switch(SessionRef(SessionId("session-b")), _context("session-b"))
    token = prepared.value["token"]

    denied = service.commit_switch(token, _context("session-b", owner="attacker"))
    accepted = service.commit_switch(token, _context("session-b"))
    replay = service.commit_switch(token, _context("session-b"))

    assert denied.diagnostics[0].code == "PREPARED_SESSION_OWNER_MISMATCH"
    assert accepted.outcome is OperationOutcome.COMPLETED
    assert replay.diagnostics[0].code == "PREPARED_SESSION_INVALID"


@pytest.mark.parametrize("terminal_state", [JobState.COMPLETED, JobState.CANCELLED])
def test_late_old_session_terminal_event_is_persistable_but_never_projected_as_active(
    terminal_state: JobState,
) -> None:
    old = SessionAggregate(_snapshot("session-a"))
    projections: list[str | None] = []
    persisted_events: list[str] = []
    service = SessionLifecycleService(
        _Repository([_snapshot("session-a"), _snapshot("session-b")]),
        _UnitFactory(),
        active=ActiveSession(old, old.revision),
        projection=lambda item: projections.append(None if item is None else item.ref.identity.value),
        event_sink=lambda item, result: persisted_events.append(item.ref.identity.value),
        token_factory=lambda: "token",
    )
    prepared = service.prepare_switch(SessionRef(SessionId("session-b")), _context("session-b"))
    assert service.commit_switch(prepared.value["token"], _context("session-b")).is_success
    old_snapshot = old.snapshot()

    result = service.route_runtime_event(
        SessionRuntimeEvent(
            SessionEventKind.JOB_STATE,
            old_snapshot.owner,
            "run-session-a",
            old_snapshot.revision,
            1,
            "job-session-a",
            terminal_state,
        )
    )

    assert result.applied
    assert persisted_events == ["session-a"]
    assert projections == ["session-b"]
    assert service.active is not None
    assert service.active.aggregate.ref.identity.value == "session-b"


def test_loaded_internal_id_or_owner_spoof_is_rejected_before_commit() -> None:
    target = _snapshot("session-b")
    repository = _Repository([target])
    repository.snapshots["session-b"] = _snapshot("session-c")
    service = SessionLifecycleService(repository, _UnitFactory())

    result = service.prepare_switch(SessionRef(SessionId("session-b")), _context("session-b"))

    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "SESSION_REFERENCE_MISMATCH"
    assert service.active is None


def test_request_project_variant_scope_cannot_open_an_owner_session_from_another_scope() -> None:
    target = _snapshot("session-b")
    scoped_owner = OwnerRef("owner", "gui", "project-a", "variant-a", "session-b")
    target = replace(
        target,
        owner=scoped_owner,
        project_id=ProjectId("project-a"),
        variant_id=VariantId("variant-a"),
    )
    service = SessionLifecycleService(_Repository([target]), _UnitFactory())
    context = RequestContext(
        "owner",
        run_id="switch-run",
        project_id="project-other",
        variant_id="variant-a",
        session_id="session-b",
    )

    result = service.prepare_switch(SessionRef(SessionId("session-b")), context)

    assert result.diagnostics[0].code == "SESSION_CONTEXT_SCOPE_MISMATCH"
    assert service.active is None


def test_event_sink_failure_is_structured_and_does_not_claim_silent_persistence() -> None:
    aggregate = SessionAggregate(_snapshot("session-a"))
    service = SessionLifecycleService(
        _Repository([aggregate.snapshot()]),
        _UnitFactory(),
        active=ActiveSession(aggregate, aggregate.revision),
        event_sink=lambda snapshot, result: (_ for _ in ()).throw(OSError("disk failed")),
    )
    snapshot = aggregate.snapshot()

    result = service.route_runtime_event(
        SessionRuntimeEvent(
            SessionEventKind.JOB_STATE,
            snapshot.owner,
            "run-session-a",
            snapshot.revision,
            1,
            "job-session-a",
            JobState.COMPLETED,
        )
    )

    assert result.applied
    assert result.diagnostic is not None
    assert result.diagnostic.code == "SESSION_EVENT_SINK_FAILED"


def test_500_create_switch_destroy_cycles_release_session_registry_and_listeners() -> None:
    snapshots = [_snapshot(f"session-{index}") for index in range(500)]
    repository = _Repository(snapshots)
    service = SessionLifecycleService(
        repository,
        _UnitFactory(),
        token_factory=(f"token-{index}" for index in range(500)).__next__,
    )
    previous = None
    aggregates: list[SessionAggregate] = []
    for snapshot in snapshots:
        target = snapshot.ref
        prepared = service.prepare_switch(target, _context(target.identity.value))
        assert prepared.is_success
        assert service.commit_switch(prepared.value["token"], _context(target.identity.value)).is_success
        assert service.active is not None
        service.active.aggregate.subscribe(lambda current: None)
        aggregates.append(service.active.aggregate)
        if previous is not None:
            service.detach_session(previous)
        previous = target
    assert previous is not None
    service.detach_session(previous)

    assert service.active is None
    assert service.retained_session_count == 0
    assert len(repository.load_calls) == 500
    assert all(aggregate.listener_count == 0 for aggregate in aggregates)
