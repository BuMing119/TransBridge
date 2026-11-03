"""Navigation may proceed after translations were durably saved with missing input."""

from dataclasses import replace

import pytest

from tests.application.projects.test_lifecycle import _active, _context, _harness, _refs
from transbridge.application.contracts import Diagnostic, OperationOutcome
from transbridge.application.projects import DirtyDecision, TransitionTarget
from transbridge.application.projects.source_inputs import MissingProjectSourceInput


@pytest.mark.parametrize("closing", [True, False])
def test_saved_missing_source_allows_transition_without_retrying_save(closing):
    project_ref, variant_ref = _refs("current", "current-variant")
    active = replace(
        _active(project_ref, variant_ref, revision=2, persisted_revision=1),
        missing_inputs=(
            MissingProjectSourceInput("source", None, "Source", None, Diagnostic("SOURCE_MISSING", "缺少来源")),
        ),
    )
    target_project, target_variant = _refs("next", "next-variant")
    target = TransitionTarget(None, None) if closing else TransitionTarget(target_project, target_variant)
    harness = _harness(active, {"next": _active(target_project, target_variant)})

    prepared = harness.service.prepare_transition(target, _context(), dirty_decision=DirtyDecision.SAVE)

    assert prepared.outcome is OperationOutcome.COMPLETED, prepared.diagnostics
    assert harness.store.saved_revisions == [2]
    assert not harness.service.active.dirty
    committed = harness.service.commit_transition(prepared.value["token"], _context())
    assert committed.is_success, committed.diagnostics
    assert harness.store.saved_revisions == [2]
    if closing:
        assert harness.service.active is None
    else:
        assert harness.service.active.project_ref == target_project


def test_missing_source_does_not_allow_transition_after_failed_persistence():
    project_ref, variant_ref = _refs("current", "current-variant")
    active = replace(
        _active(project_ref, variant_ref, revision=2, persisted_revision=1),
        missing_inputs=(
            MissingProjectSourceInput("source", None, "Source", None, Diagnostic("SOURCE_MISSING", "缺少来源")),
        ),
    )
    harness = _harness(active, {})
    harness.store.fail_commit = True

    result = harness.service.prepare_transition(
        TransitionTarget(None, None), _context(), dirty_decision=DirtyDecision.SAVE
    )

    assert result.outcome is OperationOutcome.FAILED
    assert harness.service.active.dirty
    assert harness.store.saved_revisions == []
