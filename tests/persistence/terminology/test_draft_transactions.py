from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import sqlite3

import pytest

from tests.contracts.terminology.test_repository_contract import _build, _version
from transbridge.application.terminology.conflicts import (
    DecisionEvidenceReconciliation,
    EvidenceReconciliationStatus,
)
from transbridge.application.terminology.drafts import (
    DraftLineState,
    DraftWriteConflict,
    DraftWriteExpectation,
    new_draft,
    revised_draft,
)
from transbridge.application.terminology.errors import RepositoryConflictError
from transbridge.application.terminology.models import (
    DecisionStatus,
    ManualAction,
    ManualActionType,
    TermDecision,
)
from transbridge.persistence.terminology import SqliteTerminologyRepository, TerminologyStorageError


@dataclass
class _LineReader:
    line: DraftLineState
    calls_in_transaction: int = 0

    def read_line(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        variant_id: str,
    ) -> DraftLineState:
        assert connection.in_transaction
        assert (project_id, variant_id) == (self.line.project_id, self.line.variant_id)
        self.calls_in_transaction += 1
        return self.line


def _line(
    *,
    variant_revision: int = 1,
    version_id: str | None = None,
    content_digest: str = "no-effective-version",
) -> DraftLineState:
    return DraftLineState("project-1", "variant-1", variant_revision, version_id, content_digest)


def _action(action_id: str) -> ManualAction:
    return ManualAction(
        action_id,
        "term-1",
        ManualActionType.ADD,
        "human:tester",
        "2026-08-28T00:00:00+00:00",
        None,
        None,
        f"after:{action_id}",
    )


def _empty_draft(draft_id: str, line: DraftLineState, *, actions: tuple[ManualAction, ...] = ()):
    return new_draft(
        draft_id=draft_id,
        project_id=line.project_id,
        variant_id=line.variant_id,
        base_version_id=line.effective_version_id,
        base_content_digest=line.effective_content_digest,
        actions=actions,
    )


def test_save_compares_complete_line_and_rolls_back_draft_and_action_together(tmp_path: Path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        reader = _LineReader(_line())
        transactions = repository.draft_transactions(reader)
        initial = _empty_draft("draft-1", reader.line)
        transactions.create_draft(initial, expected_line=reader.line, historical_base=False)

        saved = revised_draft(initial, actions=(_action("action-1"),))
        transactions.save_draft(saved, expectation=DraftWriteExpectation.from_draft(initial, reader.line))
        assert repository.active_draft("project-1", "variant-1") == saved
        assert repository.list_manual_actions(saved.ref).items == saved.actions

        repository._connection.execute(
            "CREATE TRIGGER reject_action_2 BEFORE INSERT ON draft_actions "
            "WHEN NEW.stable_id = 'action-2' BEGIN SELECT RAISE(ABORT, 'injected action failure'); END"
        )
        failed = revised_draft(saved, actions=(*saved.actions, _action("action-2")))
        with pytest.raises(TerminologyStorageError):
            transactions.save_draft(failed, expectation=DraftWriteExpectation.from_draft(saved, reader.line))

        assert repository.active_draft("project-1", "variant-1") == saved
        assert repository.list_manual_actions(saved.ref).items == saved.actions
        repository._connection.execute("DROP TRIGGER reject_action_2")

        erased = revised_draft(saved, actions=())
        with pytest.raises(RepositoryConflictError, match="append-only"):
            transactions.save_draft(erased, expectation=DraftWriteExpectation.from_draft(saved, reader.line))

        stale_expectation = DraftWriteExpectation.from_draft(saved, reader.line)
        reader.line = replace(reader.line, variant_revision=2)
        with pytest.raises(DraftWriteConflict, match="line changed"):
            transactions.save_draft(failed, expectation=stale_expectation)
        assert repository.active_draft("project-1", "variant-1") == saved
        assert reader.calls_in_transaction >= 4
    finally:
        repository.close()


def test_replace_and_abandon_preserve_guard_and_carried_action_history(tmp_path: Path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        reader = _LineReader(_line())
        transactions = repository.draft_transactions(reader)
        initial = _empty_draft("draft-1", reader.line, actions=(_action("action-1"),))
        transactions.create_draft(initial, expected_line=reader.line, historical_base=False)
        replacement = _empty_draft("draft-2", reader.line, actions=initial.actions)

        transactions.replace_draft(
            initial.ref,
            replacement,
            expectation=DraftWriteExpectation.from_draft(initial, reader.line),
        )

        assert transactions.active_draft("project-1", "variant-1") == replacement
        assert repository.list_manual_actions(replacement.ref).items == initial.actions
        transactions.abandon_draft(
            replacement.ref,
            expectation=DraftWriteExpectation.from_draft(replacement, reader.line),
        )
        assert transactions.active_draft("project-1", "variant-1") is None
    finally:
        repository.close()


def test_historical_draft_requires_existing_immutable_version_and_matching_digest(tmp_path: Path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        source = _build()
        first = _version(source, "version-1")
        second = _version(source, "version-2", "version-1")
        repository.put_build(source)
        repository.publish_version(first, expected_effective_version_id=None)
        repository.publish_version(second, expected_effective_version_id="version-1")
        line = _line(version_id=second.ref.version_id, content_digest=second.ref.content_digest)
        transactions = repository.draft_transactions(_LineReader(line))

        historical = new_draft(
            draft_id="draft-history",
            project_id="project-1",
            variant_id="variant-1",
            base_version_id=first.ref.version_id,
            base_content_digest=first.ref.content_digest,
        )
        transactions.create_draft(historical, expected_line=line, historical_base=True)
        assert repository.active_draft("project-1", "variant-1") == historical
        transactions.abandon_draft(
            historical.ref,
            expectation=DraftWriteExpectation.from_draft(historical, line),
        )

        wrong_digest = new_draft(
            draft_id="draft-wrong-digest",
            project_id="project-1",
            variant_id="variant-1",
            base_version_id=first.ref.version_id,
            base_content_digest="wrong-content-digest",
        )
        with pytest.raises(RepositoryConflictError, match="identity or content digest"):
            transactions.create_draft(wrong_digest, expected_line=line, historical_base=True)

        missing = new_draft(
            draft_id="draft-missing-version",
            project_id="project-1",
            variant_id="variant-1",
            base_version_id="missing-version",
            base_content_digest="missing-content-digest",
        )
        with pytest.raises(RepositoryConflictError, match="identity or content digest"):
            transactions.create_draft(missing, expected_line=line, historical_base=True)
        assert repository.active_draft("project-1", "variant-1") is None
    finally:
        repository.close()


def test_reconciliation_updates_evidence_without_appending_manual_actions(tmp_path: Path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        line = _line()
        transactions = repository.draft_transactions(_LineReader(line))
        decision = TermDecision(
            "term-1",
            "project-1",
            "variant-1",
            "Dragon",
            "dragon",
            "龙",
            status=DecisionStatus.MANUAL_CONFIRMED,
            evidence_ids=("evidence-old",),
        )
        initial = new_draft(
            draft_id="draft-1",
            project_id="project-1",
            variant_id="variant-1",
            base_version_id=None,
            base_content_digest=line.effective_content_digest,
            decisions=(decision,),
            actions=(_action("action-1"),),
        )
        transactions.create_draft(initial, expected_line=line, historical_base=False)
        reconciled_decision = replace(
            decision,
            status=DecisionStatus.REVIEW_REQUIRED,
            evidence_ids=("evidence-new",),
        )
        reconciled = revised_draft(initial, decisions=(reconciled_decision,), actions=initial.actions)
        projection = (
            DecisionEvidenceReconciliation(
                "term-1",
                EvidenceReconciliationStatus.NEEDS_REVIEW,
                ("evidence-new",),
            ),
        )

        transactions.save_reconciliation(
            reconciled,
            reconciliation=projection,
            expectation=DraftWriteExpectation.from_draft(initial, line),
        )
        assert repository.active_draft("project-1", "variant-1") == reconciled
        assert repository.list_manual_actions(reconciled.ref).items == initial.actions

        illegal = revised_draft(reconciled, actions=(*reconciled.actions, _action("action-2")))
        with pytest.raises(RepositoryConflictError, match="cannot append ManualAction"):
            transactions.save_reconciliation(
                illegal,
                reconciliation=projection,
                expectation=DraftWriteExpectation.from_draft(reconciled, line),
            )
        assert repository.active_draft("project-1", "variant-1") == reconciled
    finally:
        repository.close()
