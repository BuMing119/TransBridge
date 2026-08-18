from __future__ import annotations

from transbridge.application.projections import ProjectionEvent, ProjectionSnapshot, ProjectionStore


def _snapshot(revision: int = 2, persisted: int = 1) -> ProjectionSnapshot:
    return ProjectionSnapshot(
        "variant:main",
        revision,
        persisted,
        {"labels": {"entry": ["review"]}, "nested": [["value"]]},
    )


def test_snapshot_is_defensive_and_dirty_is_revision_derived() -> None:
    store = ProjectionStore(_snapshot())
    projected = store.snapshot()
    assert projected is not None and projected.dirty
    values = projected.to_dict()["values"]
    values["nested"][0][0] = "mutated"

    assert store.snapshot().to_dict()["values"]["nested"] == [["value"]]
    assert store.mark_persisted(2).snapshot.dirty is False


def test_duplicate_stale_gap_and_stream_mismatch_never_mutate_projection() -> None:
    store = ProjectionStore(_snapshot())
    applied = store.apply(ProjectionEvent("variant:main", 3, 1, {"value": 3}, "event-3"))
    duplicate = store.apply(ProjectionEvent("variant:main", 3, 1, {"value": 999}, "event-3"))
    stale = store.apply(ProjectionEvent("variant:main", 2, 1, {"value": 2}, "event-2"))
    gap = store.apply(ProjectionEvent("variant:main", 5, 1, {"value": 5}, "event-5"))
    other = store.apply(ProjectionEvent("variant:other", 4, 1, {"value": 4}, "other"))

    assert applied.applied
    assert duplicate.diagnostics[0].code == "PROJECTION_EVENT_DUPLICATE"
    assert stale.diagnostics[0].code == "PROJECTION_EVENT_STALE"
    assert gap.diagnostics[0].code == "PROJECTION_EVENT_GAP"
    assert other.diagnostics[0].code == "PROJECTION_STREAM_MISMATCH"
    assert store.snapshot().to_dict()["values"] == {"value": 3}


def test_gap_can_rebuild_from_full_aggregate_snapshot() -> None:
    store = ProjectionStore(_snapshot())
    assert not store.apply(ProjectionEvent("variant:main", 7, 1, {"lost": True}, "event-7")).applied

    rebuilt = store.rebuild(ProjectionSnapshot("variant:main", 7, 7, {"canonical": True}))

    assert rebuilt.applied
    assert rebuilt.snapshot.to_dict()["values"] == {"canonical": True}
    assert not rebuilt.snapshot.dirty


def test_listener_failure_is_diagnostic_and_subscriptions_are_idempotently_released() -> None:
    store = ProjectionStore()
    calls = []
    good = store.subscribe(lambda snapshot: calls.append(snapshot), replay=False)
    bad = store.subscribe(lambda snapshot: (_ for _ in ()).throw(RuntimeError("ui failed")), replay=False)

    decision = store.rebuild(_snapshot())
    bad.close()
    bad.close()
    good.close()

    assert decision.applied
    assert decision.diagnostics[0].code == "PROJECTION_LISTENER_FAILED"
    assert len(calls) == 1
    assert store.listener_count == 0


def test_500_subscribe_rebuild_close_cycles_do_not_retain_listeners() -> None:
    store = ProjectionStore()
    for revision in range(500):
        subscription = store.subscribe(lambda snapshot: None, replay=False)
        store.rebuild(ProjectionSnapshot("variant:main", revision, revision, {"revision": revision}))
        subscription.close()
    assert store.listener_count == 0
