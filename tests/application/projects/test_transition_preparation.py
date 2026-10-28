"""Detached view preparation and stale-open guards preserve the active lifecycle."""

from dataclasses import replace

import pytest

from tests.application.projects.test_lifecycle import _active, _context, _harness, _refs
from transbridge.application.projects import DirtyDecision, GuiProjectCommandFacade, TransitionTarget
from transbridge.application.projects.transition_preparation import active_signature
from transbridge.persistence.v2.variant import VariantChangeSet


def _opening(*, dirty=False, active=True):
    old_project, old_variant = _refs("old-project", "old-variant")
    new_project, new_variant = _refs("new-project", "new-variant")
    old = _active(old_project, old_variant, revision=2, persisted_revision=1 if dirty else 2) if active else None
    candidate = _active(new_project, new_variant)
    return _harness(old, {new_project.identity.value: candidate}), old, TransitionTarget(new_project, new_variant)


def test_detached_callback_receives_validated_candidate_before_activation():
    harness, old, target = _opening()
    prepared_views = []

    def prepare_view(candidate):
        assert harness.service.active is old
        assert harness.store.calls == []
        assert candidate.project.envelope.data["active_variant_id"] == target.variant_ref.identity.value
        assert candidate.leases[0].owner_id == _context().owner_id
        prepared_views.append(candidate.summary())

    prepared = harness.service.prepare_transition(target, _context(), prepare_candidate=prepare_view)

    assert prepared.is_success
    assert prepared_views == [prepared.value["candidate"]]
    assert harness.service.active is old
    assert harness.store.calls == []
    assert harness.service.commit_transition(prepared.value["token"], _context()).is_success
    assert harness.store.active_pointer == (target.project_ref.identity.value, target.variant_ref.identity.value)


def test_failed_detached_callback_releases_lease_without_consuming_token_or_activating():
    harness, old, target = _opening()

    def broken_view(_candidate):
        raise ValueError("view data cannot be prepared")

    failed = harness.service.prepare_transition(target, _context(), prepare_candidate=broken_view)

    assert failed.diagnostics[0].code == "LIFECYCLE_PREPARE_FAILED"
    assert harness.service.active is old
    assert harness.service.generation == 0
    assert harness.store.calls == []
    assert harness.leases.released == [("lease-1",)]
    assert not harness.service.commit_transition("token-0", _context()).is_success
    next_preparation = harness.service.prepare_transition(target, _context())
    assert next_preparation.is_success
    assert next_preparation.value["token"] == "token-0"
    assert harness.service.discard_prepared("token-0", _context()).is_success
    assert harness.leases.released == [("lease-1",), ("lease-2",)]


def test_invalid_candidate_never_reaches_detached_callback():
    harness, old, target = _opening()
    harness.loader.candidates[target.project_ref.identity.value] = old
    prepared_views = []

    result = harness.service.prepare_transition(target, _context(), prepare_candidate=prepared_views.append)

    assert not result.is_success
    assert prepared_views == []
    assert harness.leases.released == [("lease-1",)]
    assert harness.store.calls == []


@pytest.mark.parametrize("decision", [DirtyDecision.SAVE, DirtyDecision.DISCARD])
def test_edit_during_source_preparation_rejects_old_dirty_decision_before_save_or_discard(decision):
    harness, old, target = _opening(dirty=True)
    expected = active_signature(old)
    snapshot = old.variant.snapshot()
    context = _context()
    edit = VariantChangeSet(
        snapshot.ref,
        snapshot.revision,
        snapshot.source_fingerprints,
        snapshot.entries,
        snapshot.label_library,
        context.run_id,
    )
    assert harness.service.commit_active_variant(
        edit, context, expected_project_revision=old.project.envelope.revision
    ).is_success
    prepared_views = []

    result = harness.service.prepare_transition(
        target,
        context,
        dirty_decision=decision,
        expected_active_signature=expected,
        prepare_candidate=prepared_views.append,
    )

    assert result.diagnostics[0].code == "PREPARED_TRANSITION_STALE"
    assert harness.service.active is old
    assert old.dirty
    assert old.variant.revision == snapshot.revision + 1
    assert harness.store.calls == []
    assert harness.loader.calls == []
    assert harness.leases.acquired == []
    assert prepared_views == []


def test_explicit_empty_signature_refuses_a_project_opened_since_capture():
    harness, old, target = _opening()

    guarded = harness.service.prepare_transition(target, _context(), expected_active_signature=None)
    unguarded = harness.service.prepare_transition(target, _context())

    assert guarded.diagnostics[0].code == "PREPARED_TRANSITION_STALE"
    assert unguarded.is_success
    assert harness.service.active is old
    assert harness.service.discard_prepared(unguarded.value["token"], _context()).is_success


def test_explicit_empty_signature_allows_initial_open_and_close_callback_receives_none():
    harness, _old, target = _opening(active=False)
    opened = harness.service.prepare_transition(target, _context(), expected_active_signature=None)
    assert opened.is_success
    assert harness.service.commit_transition(opened.value["token"], _context()).is_success
    seen = []

    closed = harness.service.prepare_transition(
        TransitionTarget.close(),
        _context(),
        expected_active_signature=active_signature(harness.service.active),
        prepare_candidate=seen.append,
    )

    assert closed.is_success
    assert seen == [None]
    assert harness.service.commit_transition(closed.value["token"], _context()).is_success
    assert harness.service.active is None


def test_facade_forwards_guard_and_callback_without_committing_on_failure():
    harness, old, target = _opening(dirty=True)
    facade = GuiProjectCommandFacade(harness.service)
    changed_project = replace(old.project, envelope=replace(old.project.envelope, revision=9))
    expected = active_signature(replace(old, project=changed_project))
    seen = []

    stale = facade.switch_v2(
        target.project_ref,
        target.variant_ref,
        _context(),
        dirty_decision=DirtyDecision.DISCARD,
        expected_active_signature=expected,
        prepare_candidate=seen.append,
    )

    assert stale.diagnostics[0].code == "PREPARED_TRANSITION_STALE"
    assert seen == []
    assert harness.store.calls == []
    assert harness.leases.acquired == []

    def broken_view(_candidate):
        raise ValueError("cannot build view")

    failed = facade.switch_v2(
        target.project_ref,
        target.variant_ref,
        _context(),
        dirty_decision=DirtyDecision.DISCARD,
        expected_active_signature=active_signature(old),
        prepare_candidate=broken_view,
    )
    assert not failed.is_success
    assert harness.service.active is old
    assert old.dirty
    assert harness.store.calls == []
    assert harness.leases.released == [("lease-1",)]
