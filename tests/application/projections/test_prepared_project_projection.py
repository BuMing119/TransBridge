"""Candidate views share frozen rows with publication without retaining failed candidates."""

from dataclasses import replace
from types import SimpleNamespace
import weakref

import pytest

from tests.application.projections.test_project_entry_reuse import _count_serializations, _snapshot
from tests.application.projects.test_lifecycle import _context, _harness
from transbridge.application.projections import project_entries
from transbridge.application.projections.adapters import ProjectProjectionPublisher
from transbridge.application.projections.store import ProjectionStore
from transbridge.application.projects import TransitionTarget
from transbridge.persistence.v2.variant import VariantAggregate


def test_open_prepares_and_publishes_each_frozen_entry_once(monkeypatch):
    variant, candidate = _snapshot(30)
    candidate = replace(candidate, variant=VariantAggregate(replace(variant, revision=1)))
    store = ProjectionStore()
    publisher = ProjectProjectionPublisher(store)
    harness = _harness(None, {candidate.project_ref.identity.value: candidate}, event=publisher)
    publisher.bind(harness.service)
    calls = _count_serializations(monkeypatch)
    frozen_entries = []
    freeze = project_entries.freeze_projection_value

    def counted_freeze(value):
        if isinstance(value, dict) and "entry_key" in value:
            frozen_entries.append(value["entry_key"])
        return freeze(value)

    monkeypatch.setattr(project_entries, "freeze_projection_value", counted_freeze)
    views = []
    try:
        prepared = harness.service.prepare_transition(
            TransitionTarget(candidate.project_ref, candidate.formal_variant_ref),
            _context(),
            prepare_candidate=lambda active: views.append(publisher.prepare(active)),
        )
        assert prepared.is_success and store.snapshot() is None
        before = views[0]
        assert before.dirty
        assert harness.service.commit_transition(prepared.value["token"], _context()).is_success
    finally:
        publisher.discard_prepared()

    after = store.snapshot()
    assert len(calls) == len(frozen_entries) == len(variant.entries)
    assert not after.dirty and after.values["dirty"] is False
    assert after.values["entries"] is before.values["entries"]
    assert before.dirty and before.values["dirty"] is True
    with pytest.raises(TypeError):
        next(iter(after.values["entries"]))["translation"] = "outside edit"
    copied = after.to_dict()
    copied["values"]["entries"][0]["translation"] = "outside edit"
    assert next(iter(after.values["entries"]))["translation"] == "original"


def test_failed_candidate_view_keeps_current_rows_and_discards_candidate(monkeypatch):
    _, current = _snapshot()
    variant, candidate = _snapshot(3)
    candidate = replace(
        candidate,
        variant=VariantAggregate(
            replace(variant, entries=tuple(replace(e, translation="candidate") for e in variant.entries))
        ),
    )
    store = ProjectionStore()
    publisher = ProjectProjectionPublisher(store)
    harness = _harness(current, {candidate.project_ref.identity.value: candidate}, event=publisher)
    publisher.bind(harness.service)
    before = store.snapshot()
    calls = _count_serializations(monkeypatch)

    def prepare_view(active):
        publisher.prepare(active)
        raise ValueError("view preparation failed")

    try:
        result = harness.service.prepare_transition(
            TransitionTarget(candidate.project_ref, candidate.formal_variant_ref),
            _context(),
            prepare_candidate=prepare_view,
        )
        assert not result.is_success
    finally:
        publisher.discard_prepared()

    assert store.snapshot() is before and harness.service.active is current
    calls.clear()
    publisher.rebuild()
    assert calls == []
    assert store.snapshot().values["entries"] is before.values["entries"]


def test_prepare_error_itself_does_not_poison_active_rows(monkeypatch):
    _, active = _snapshot()
    publisher = ProjectProjectionPublisher(ProjectionStore(), SimpleNamespace(active=active))
    publisher.rebuild()
    before = publisher.store.snapshot()
    active.project.envelope.data["sources"] = [{"bad": object()}]
    with pytest.raises(ValueError, match="finite JSON"):
        publisher.prepare(active)
    active.project.envelope.data["sources"] = []
    calls = _count_serializations(monkeypatch)
    publisher.rebuild()
    assert calls == []
    assert publisher.store.snapshot().values["entries"] is before.values["entries"]


def test_same_revision_reopen_uses_new_content_and_current_project_metadata(monkeypatch):
    variant, active = _snapshot(3)
    lifecycle = SimpleNamespace(active=active)
    publisher = ProjectProjectionPublisher(ProjectionStore(), lifecycle)
    publisher.rebuild()
    before = publisher.store.snapshot()
    replaced = replace(variant, entries=tuple(replace(e, translation="reopened") for e in variant.entries))
    candidate = replace(active, variant=VariantAggregate(replaced))
    calls = _count_serializations(monkeypatch)
    view = publisher.prepare(candidate)
    candidate.project.envelope.data["name"] = "changed after preparation"
    lifecycle.active = candidate
    publisher.rebuild()
    publisher.discard_prepared()
    after = publisher.store.snapshot()

    assert before.revision == after.revision
    assert len(calls) == 3
    assert after.values["entries"] is view.values["entries"]
    assert after.to_dict()["values"]["entries"][0]["translation"] == "reopened"
    assert after.values["project_name"] == "changed after preparation"
    assert before.to_dict()["values"]["entries"][0]["translation"] == "original"


def test_unrelated_commit_does_not_adopt_candidate_at_same_revision(monkeypatch):
    variant, candidate = _snapshot(3)
    lifecycle = SimpleNamespace(active=None)
    publisher = ProjectProjectionPublisher(ProjectionStore(), lifecycle)
    publisher.prepare(candidate)
    other = replace(
        candidate,
        variant=VariantAggregate(
            replace(variant, entries=tuple(replace(e, translation="other commit") for e in variant.entries))
        ),
    )
    calls = _count_serializations(monkeypatch)
    lifecycle.active = other
    publisher.rebuild()
    publisher.discard_prepared()

    assert len(calls) == 3
    assert publisher.store.snapshot().to_dict()["values"]["entries"][0]["translation"] == "other commit"


def test_only_current_and_one_candidate_are_retained_and_close_releases_rows():
    _, active = _snapshot()
    lifecycle = SimpleNamespace(active=active)
    publisher = ProjectProjectionPublisher(ProjectionStore(), lifecycle)
    publisher.rebuild()
    current = publisher._entry_projector
    publisher.prepare(active)
    first_candidate = weakref.ref(publisher._prepared[1])
    publisher.prepare(active)
    assert first_candidate() is None
    second_candidate = weakref.ref(publisher._prepared[1])
    publisher.discard_prepared()
    assert second_candidate() is None
    assert publisher._entry_projector is current
    publisher.prepare(active)
    closing_candidate = weakref.ref(publisher._prepared[1])
    lifecycle.active = None
    publisher.rebuild()
    assert publisher.store.snapshot() is None
    assert current._snapshot is None and current._by_key == {} and current.rows(None) == ()
    assert closing_candidate() is None
