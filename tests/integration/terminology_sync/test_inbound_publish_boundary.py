from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sqlite3

import pytest

from tests.application.terminology.story08_support import Permit, State, build
from transbridge.ai_translator.term_formats import TermEntry
from transbridge.application.contracts import RequestContext
from transbridge.application.ports.paratranz_terms import ParaTranzTerm, ParaTranzTermSnapshot
from transbridge.application.terminology.decisions import ManualActor
from transbridge.application.terminology.drafts import DraftLineState, new_draft
from transbridge.application.terminology.effective import EffectiveSnapshotStatus
from transbridge.application.terminology.publish import PublishTerminologyRequest, VersionPublisher
from transbridge.application.terminology.workloads import TerminologyExpectedState
from transbridge.application.terminology_sync.draft_import import InboundDraftImportService
from transbridge.application.terminology_sync.draft_import_models import DraftImportChoice, DraftImportSelection
from transbridge.application.terminology_sync.executor import (
    ExecuteTerminologyBackupRequest,
    TerminologyBackupExecutor,
)
from transbridge.application.terminology_sync.identity import sync_line_id
from transbridge.application.terminology_sync.inbound import InboundReviewDecision, InboundReviewStatus
from transbridge.application.terminology_sync.inbound_service import DurableTerminologyInboundService
from transbridge.application.terminology_sync.models import (
    TerminologySyncLine,
    TerminologySyncMode,
    TerminologySyncProfile,
    TerminologySyncTarget,
)
from transbridge.application.terminology_sync.planner import TerminologySyncPlanner, TerminologySyncPlannerInput
from transbridge.application.terminology_sync.use_case import AuthorizedTerminologySyncPlan
from transbridge.persistence.terminology import (
    SqliteEffectiveTerminologySnapshotPort,
    SqliteTerminologyRepository,
    TerminologyStorageError,
)

_NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
_PROJECT_ID = "project-1"
_VARIANT_ID = "variant-1"


@dataclass
class _DraftState:
    line: DraftLineState
    repository: SqliteTerminologyRepository

    def read_line(self, connection: sqlite3.Connection, project_id: str, variant_id: str) -> DraftLineState:
        assert connection.in_transaction
        assert (project_id, variant_id) == (self.line.project_id, self.line.variant_id)
        return self.line

    def current_line(self, project_id: str, variant_id: str) -> DraftLineState:
        assert (project_id, variant_id) == (self.line.project_id, self.line.variant_id)
        return self.line

    def effective_decisions(self, line: DraftLineState):
        version = self.repository.effective_version(line.project_id, line.variant_id)
        return () if version is None else version.decisions


class _Actor:
    @staticmethod
    def resolve(context: RequestContext) -> ManualActor:
        return ManualActor(f"human:{context.owner_id}", True)


class _Clock:
    @staticmethod
    def now() -> datetime:
        return _NOW


class _Ids:
    def __init__(self) -> None:
        self._next = 0

    def new_id(self) -> str:
        self._next += 1
        return f"generated-{self._next}"


class _FreshInputs:
    def __init__(self, inputs: TerminologySyncPlannerInput) -> None:
        self.inputs = inputs

    def load_for_plan(self, plan_hash: str) -> TerminologySyncPlannerInput:
        assert TerminologySyncPlanner().plan(self.inputs).plan_hash == plan_hash
        return self.inputs


class _RemoteReadOnly:
    @staticmethod
    def create_term(*args, **kwargs):
        raise AssertionError("remote-only inbound planning must not create a ParaTranz term")

    @staticmethod
    def update_term(*args, **kwargs):
        raise AssertionError("remote-only inbound planning must not update a ParaTranz term")

    @staticmethod
    def delete_term(*args, **kwargs):
        raise AssertionError("remote-only inbound planning must not delete a ParaTranz term")


def _publish_initial_empty_version(repository: SqliteTerminologyRepository):
    source = build()
    repository.put_build(source)
    draft = new_draft(
        draft_id="initial-empty-draft",
        project_id=_PROJECT_ID,
        variant_id=_VARIANT_ID,
        base_version_id=None,
        base_content_digest="no-project-version",
    )
    repository.create_draft(draft)
    expected = TerminologyExpectedState(
        1,
        1,
        "initial-source-graph",
        "initial-source-fingerprints",
        effective_version_id=None,
        base_version_id=None,
        draft_id=draft.ref.draft_id,
        draft_revision=draft.ref.revision,
        build_freshness_digest="current",
    )
    VersionPublisher(repository.publisher, State(expected), Permit()).publish(
        PublishTerminologyRequest(
            project_id=_PROJECT_ID,
            variant_id=_VARIANT_ID,
            expected=expected,
            build_ref=source.ref,
            draft_ref=draft.ref,
            version_id="version-base",
            published_at="2026-08-30T10:00:00+00:00",
        )
    )
    return source


def _bidirectional_inputs(repository: SqliteTerminologyRepository):
    target = TerminologySyncTarget("https://example.com/api", 7, 41)
    line_id = sync_line_id(
        project_id=_PROJECT_ID,
        variant_id=_VARIANT_ID,
        target_identity=target.target_id,
        profile_revision=0,
    )
    line = TerminologySyncLine(
        line_id,
        _PROJECT_ID,
        _VARIANT_ID,
        target,
        0,
        _NOW.isoformat(),
    )
    profile = TerminologySyncProfile(line_id, 0, mode=TerminologySyncMode.BIDIRECTIONAL)
    repository.sync_state.activate_line(line, profile)
    remote = ParaTranzTerm(
        101,
        TermEntry("Shield", "盾", "paratranz"),
        "remote-revision-1",
        "b" * 64,
        {"createdAt": "2026-08-30T00:00:00Z"},
    )
    remote_snapshot = ParaTranzTermSnapshot(41, (remote,), "a" * 64, _NOW, True)
    local_snapshot = SqliteEffectiveTerminologySnapshotPort(repository).snapshot(_PROJECT_ID, _VARIANT_ID)
    assert local_snapshot.status is EffectiveSnapshotStatus.READY
    assert local_snapshot.decisions == ()
    return line, TerminologySyncPlannerInput(
        line,
        profile,
        local_snapshot,
        remote_snapshot,
        binding_revision=1,
    )


def _executor(repository: SqliteTerminologyRepository, inputs: TerminologySyncPlannerInput):
    plan = TerminologySyncPlanner().plan(inputs)
    executor = TerminologyBackupExecutor(
        _RemoteReadOnly(),
        repository.sync_state,
        _FreshInputs(inputs),
        clock=lambda: _NOW,
        inbound_store=repository.inbound_reviews,
    )
    return plan, executor


def test_remote_only_inbound_changes_draft_but_not_effective_until_explicit_publish(tmp_path: Path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), _PROJECT_ID)
    try:
        source = _publish_initial_empty_version(repository)
        effective = SqliteEffectiveTerminologySnapshotPort(repository)
        before_import = effective.snapshot(_PROJECT_ID, _VARIANT_ID)
        line, inputs = _bidirectional_inputs(repository)
        plan, executor = _executor(repository, inputs)

        executor.execute(
            ExecuteTerminologyBackupRequest(
                AuthorizedTerminologySyncPlan(plan, "owner-1", "NOT_REQUIRED"),
                "run-bidirectional-1",
            )
        )

        baseline = repository.sync_state.get_baseline(line.line_id)
        assert baseline is not None and baseline.completed_run_id == "run-bidirectional-1"
        change_sets = repository.inbound_reviews.list_change_sets(_PROJECT_ID, _VARIANT_ID)
        assert len(change_sets) == 1
        change_set = change_sets[0]
        assert change_set.source_run_id == "run-bidirectional-1"
        assert change_set.remote_snapshot_digest == baseline.remote_snapshot_digest
        assert (
            repository.inbound_reviews.get_review_state(change_set.change_set_id).status is InboundReviewStatus.PENDING
        )

        draft_line = DraftLineState(
            _PROJECT_ID,
            _VARIANT_ID,
            1,
            before_import.version_id,
            before_import.content_digest,
        )
        draft_state = _DraftState(draft_line, repository)
        importer = InboundDraftImportService(
            repository.inbound_reviews,
            repository.draft_transactions(draft_state),
            draft_state,
            _Actor(),
            _Clock(),
            _Ids(),
        )
        inbound = DurableTerminologyInboundService(repository.inbound_reviews, importer)
        item = change_set.items[0]
        selection = DraftImportSelection(
            change_set.change_set_id,
            change_set.content_digest,
            0,
            draft_line,
            (DraftImportChoice(item.item_id, InboundReviewDecision.EDIT, edited=item.remote),),
        )
        context = RequestContext("owner-1", project_id=_PROJECT_ID, variant_id=_VARIANT_ID)
        proposal = inbound.preview_import(selection)
        imported = inbound.commit_import(proposal, context)

        active = repository.active_draft(_PROJECT_ID, _VARIANT_ID)
        assert active is not None and active.ref == imported.draft_ref
        assert active.decisions[0].translation == "盾"
        after_import = effective.snapshot(_PROJECT_ID, _VARIANT_ID)
        assert after_import.snapshot_identity == before_import.snapshot_identity
        assert after_import.content_digest == before_import.content_digest

        publish_state = TerminologyExpectedState(
            2,
            draft_line.variant_revision,
            "publish-source-graph",
            "publish-source-fingerprints",
            effective_version_id=before_import.version_id,
            base_version_id=before_import.version_id,
            draft_id=active.ref.draft_id,
            draft_revision=active.ref.revision,
            build_freshness_digest="current",
        )
        VersionPublisher(repository.publisher, State(publish_state), Permit()).publish(
            PublishTerminologyRequest(
                project_id=_PROJECT_ID,
                variant_id=_VARIANT_ID,
                expected=publish_state,
                build_ref=source.ref,
                draft_ref=active.ref,
                version_id="version-after-inbound-review",
                published_at="2026-08-30T13:00:00+00:00",
            )
        )
        after_publish = effective.snapshot(_PROJECT_ID, _VARIANT_ID)
        assert after_publish.status is EffectiveSnapshotStatus.READY
        assert after_publish.content_digest != before_import.content_digest
        assert after_publish.decisions[0].translation == "盾"
    finally:
        repository.close()


def test_atomic_bidirectional_commit_rolls_back_run_baseline_and_inbound_facts_together(tmp_path: Path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), _PROJECT_ID)
    try:
        _publish_initial_empty_version(repository)
        line, inputs = _bidirectional_inputs(repository)
        plan, executor = _executor(repository, inputs)
        repository._connection.execute(
            "CREATE TRIGGER reject_inbound_facts BEFORE INSERT ON terminology_sync_inbound_sets "
            "BEGIN SELECT RAISE(ABORT, 'injected inbound persistence failure'); END"
        )

        with pytest.raises(TerminologyStorageError):
            executor.execute(
                ExecuteTerminologyBackupRequest(
                    AuthorizedTerminologySyncPlan(plan, "owner-1", "NOT_REQUIRED"),
                    "run-atomic-rollback",
                )
            )

        assert repository.sync_state.get_baseline(line.line_id) is None
        assert repository._connection.execute("SELECT count(*) FROM terminology_sync_runs").fetchone()[0] == 0
        assert repository._connection.execute("SELECT count(*) FROM terminology_sync_inbound_sets").fetchone()[0] == 0
    finally:
        repository.close()
