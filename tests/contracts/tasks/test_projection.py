"""Read-only JobSnapshot projection and capability-gated controls (Story S07)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from transbridge.application.tasks import (
    JobCapabilities,
    JobSpec,
    JobState,
    OwnerRef,
    RuntimeTaskProjection,
    TaskRuntime,
    job_snapshot_to_view,
)


class SequenceIds:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"projection-run-{self.value}"


class AdvancingClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 18, tzinfo=UTC)

    def now(self) -> datetime:
        self.value += timedelta(microseconds=1)
        return self.value


def _runtime() -> TaskRuntime:
    return TaskRuntime(id_generator=SequenceIds(), clock=AdvancingClock())


def _owner(*, session_id: str = "session-1") -> OwnerRef:
    return OwnerRef(owner_id="owner-1", entrypoint="gui", project_id="project-1", session_id=session_id)


def _spec(**kwargs) -> JobSpec:
    return JobSpec(
        job_type="translation",
        input_ref="variant:one",
        input_fingerprint="sha256:abc",
        display_name="Batch translation",
        capabilities=JobCapabilities(
            supports_pause=kwargs.get("pause", True),
            supports_resume=kwargs.get("pause", True),
            supports_cancel=kwargs.get("cancel", True),
            supports_checkpoint=kwargs.get("checkpoint", False),
        ),
        metadata=(("phase", "round-1"),),
    )


def test_projection_preserves_every_legacy_monitor_key_and_runtime_identity() -> None:
    runtime = _runtime()
    owner = _owner()
    ref = runtime.submit(_spec(), owner).ref
    runtime.start(ref, owner)
    snapshot = runtime.get(ref, owner)

    view = job_snapshot_to_view(snapshot)

    # The legacy TaskMonitorWidget renderer keys must all be present.
    for key in ("task_id", "status", "progress", "metadata", "created_at"):
        assert key in view
    assert view["task_id"] == snapshot.ref.job_id
    assert view["run_id"] == snapshot.ref.run_id
    assert view["status"] == "running"
    assert view["metadata"]["type"] == "translation"
    assert view["metadata"]["name"] == "Batch translation"
    assert view["metadata"]["phase"] == "round-1"
    assert view["metadata"]["run_id"] == snapshot.ref.run_id
    assert view["is_terminal"] is False
    assert view["capabilities"] == {
        "pause": True,
        "resume": True,
        "cancel": True,
        "checkpoint": False,
    }


def test_progress_is_authoritative_immutable_snapshot_state() -> None:
    runtime = _runtime()
    owner = _owner()
    ref = runtime.submit(_spec(), owner).ref
    runtime.start(ref, owner)

    before = runtime.get(ref, owner)
    after = runtime.update_progress(ref, owner, {"current": 2, "total": 5, "message": "working"})

    assert before.progress == ()
    assert dict(after.progress) == {"current": 2, "message": "working", "total": 5}
    assert after.revision == before.revision + 1
    assert job_snapshot_to_view(after)["progress"]["current"] == 2


def test_projection_is_read_only_and_controls_follow_capabilities() -> None:
    runtime = _runtime()
    owner = _owner()
    ref = runtime.submit(_spec(), owner).ref
    projection = RuntimeTaskProjection(runtime)

    controls = projection.controls(ref, owner)
    assert controls.pause_enabled is False  # queued
    assert controls.cancel_enabled is True

    runtime.start(ref, owner)
    controls = projection.controls(ref, owner)
    assert controls.pause_enabled is True
    assert controls.resume_enabled is False

    paused = projection.control(ref, owner, "pause")
    assert paused.accepted is True
    assert paused.snapshot is not None
    assert paused.snapshot.state is JobState.PAUSED

    resumed = projection.control(ref, owner, "resume")
    assert resumed.accepted is True
    assert resumed.snapshot.state is JobState.RUNNING

    cancelled = projection.control(ref, owner, "cancel")
    assert cancelled.accepted is True
    assert cancelled.snapshot.state in {JobState.CANCELLING, JobState.CANCELLED}

    # cleanup is view-local; it never touches runtime state.
    cleanup = projection.control(ref, owner, "cleanup")
    assert cleanup.accepted is True
    assert cleanup.code == "view_local"
    assert runtime.get(ref, owner).state is not None


def test_control_rejects_unsupported_action_without_runtime_effect() -> None:
    runtime = _runtime()
    owner = _owner()
    ref = runtime.submit(_spec(), owner).ref
    projection = RuntimeTaskProjection(runtime)
    runtime.start(ref, owner)

    before = runtime.get(ref, owner).revision
    result = projection.control(ref, owner, "bogus")
    assert result.accepted is False
    assert result.code == "unknown_action"
    assert runtime.get(ref, owner).revision == before


def test_control_rejects_when_capability_missing() -> None:
    runtime = _runtime()
    owner = _owner()
    ref = runtime.submit(_spec(pause=False), owner).ref
    projection = RuntimeTaskProjection(runtime)
    runtime.start(ref, owner)

    result = projection.control(ref, owner, "pause")
    assert result.accepted is False
    assert result.code == "unsupported_control"


def test_owner_filtered_listing_shows_only_matching_scope_without_manage_permission() -> None:
    runtime = _runtime()
    other = _owner(session_id="session-2")
    ref_a = runtime.submit(_spec(), _owner(session_id="session-1")).ref
    runtime.submit(_spec(), other)
    projection = RuntimeTaskProjection(runtime)

    listed = projection.list(_owner(session_id="session-1"))
    assert [item.ref.job_id for item in listed] == [ref_a.job_id]


def test_view_and_public_dict_are_the_same_projection() -> None:
    runtime = _runtime()
    owner = _owner()
    ref = runtime.submit(_spec(), owner).ref
    runtime.start(ref, owner)
    runtime.complete(ref, owner)
    snapshot = runtime.get(ref, owner)

    from transbridge.application.tasks import job_snapshot_to_public_dict

    assert job_snapshot_to_view(snapshot) == job_snapshot_to_public_dict(snapshot)
    assert snapshot.state is JobState.COMPLETED
    assert snapshot.is_terminal is True
