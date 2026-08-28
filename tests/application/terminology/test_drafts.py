from __future__ import annotations

import pytest

from tests.application.terminology.story07_support import (
    DraftTransactions,
    SequenceIds,
    draft_service,
    expectation,
    line,
    ref_tuple,
)
from transbridge.application.terminology.drafts import OpenDraftCommand, revised_draft
from transbridge.application.terminology.errors import ActiveDraftError, RepositoryConflictError, RevisionConflictError


def test_one_active_draft_and_expected_revision_save():
    current_line = line()
    transactions = DraftTransactions(current_line)
    service = draft_service(transactions)
    command = OpenDraftCommand(current_line, "version-1", "version-content-1")
    draft = service.open(command)

    with pytest.raises(ActiveDraftError):
        service.open(command)

    updated = revised_draft(draft, digest_context="save")
    assert service.save(updated, expectation=expectation(draft, current_line)).ref.revision == 1
    with pytest.raises(RevisionConflictError):
        service.save(revised_draft(updated), expectation=expectation(draft, current_line))


def test_variant_or_effective_change_rejects_silent_overwrite_and_preserves_draft():
    original_line = line()
    transactions = DraftTransactions(original_line)
    service = draft_service(transactions)
    draft = service.open(OpenDraftCommand(original_line, "version-1", "version-content-1"))
    transactions.line = line(variant_revision=2, version_id="version-2", digest="version-content-2")

    with pytest.raises(RepositoryConflictError, match="line changed"):
        service.save(revised_draft(draft), expectation=expectation(draft, original_line))
    assert transactions.draft == draft


def test_rebase_is_proposal_then_explicit_commit_with_new_identity_and_digest():
    original_line = line()
    transactions = DraftTransactions(original_line)
    ids = SequenceIds()
    service = draft_service(transactions, ids)
    draft = service.open(OpenDraftCommand(original_line, "version-1", "version-content-1"))
    target = line(variant_revision=2, version_id="version-2", digest="version-content-2")

    proposal = service.propose_rebase(expectation(draft, original_line), target)
    assert transactions.draft == draft
    assert proposal.replacement.ref.draft_id != draft.ref.draft_id
    assert proposal.replacement.ref.decision_set_digest != draft.ref.decision_set_digest

    transactions.line = target
    committed = service.commit_rebase(proposal, expectation(draft, target))
    assert committed == proposal.replacement
    assert committed.ref.revision == 0
    assert committed.ref.base_version_id == "version-2"


def test_abandon_then_from_history_creates_fresh_cache_identity_without_moving_effective():
    current_line = line(version_id="version-3", digest="content-3")
    transactions = DraftTransactions(current_line)
    service = draft_service(transactions)
    first = service.open(OpenDraftCommand(current_line, "version-3", "content-3"))
    service.abandon(expectation(first, current_line))

    historical = service.from_history(OpenDraftCommand(current_line, "version-1", "content-1"))
    assert historical.ref.revision == first.ref.revision == 0
    assert ref_tuple(historical.ref) != ref_tuple(first.ref)
    assert transactions.line == current_line
