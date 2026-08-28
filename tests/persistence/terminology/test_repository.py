from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.contracts.terminology.test_repository_contract import RepositoryContract, _build, _candidate, _version
from transbridge.application.terminology.drafts import revised_draft
from transbridge.application.terminology.errors import CursorStaleError, RepositoryConflictError
from transbridge.application.terminology.models import (
    ArtifactKind,
    ArtifactLedgerEntry,
    ArtifactStatus,
    BilingualEvidence,
    BuildSummary,
    ChangeLogDocument,
    ChangeLogDocumentRef,
    ConflictGroup,
    ConflictVariant,
    DraftRef,
    ManualAction,
    ManualActionType,
    TerminologyDraft,
    TerminologyReportSnapshot,
    TerminologyReportSnapshotRef,
)
from transbridge.application.terminology.ports import PageRequest
from transbridge.persistence.terminology import CacheKind, SqliteTerminologyRepository


class TestSqliteRepositoryContract(RepositoryContract):
    @pytest.fixture
    def repository(self, tmp_path: Path) -> SqliteTerminologyRepository:
        repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
        try:
            yield repository
        finally:
            repository.close()


def test_draft_manual_action_history_is_append_only(tmp_path: Path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        action = ManualAction(
            "action-1",
            "term-1",
            ManualActionType.ADD,
            "tester",
            "2026-08-28T00:00:00+00:00",
            None,
            None,
            "after",
        )
        draft = TerminologyDraft(
            DraftRef("draft-1", "project-1", "variant-1", None, "no-base", 0, "decision-set-0"),
            actions=(action,),
        )
        repository.create_draft(draft)

        with pytest.raises(RepositoryConflictError, match="append-only"):
            repository.update_draft(revised_draft(draft, actions=()), expected_revision=0)

        assert repository.active_draft("project-1", "variant-1") == draft
    finally:
        repository.close()


def test_repository_survives_close_and_reopen(tmp_path: Path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    build = _build()
    version = _version(build, "version-1")
    repository.put_build(build)
    repository.publish_version(version, expected_effective_version_id=None)
    repository.close()

    reopened = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        assert reopened.get_build(build.ref) == build
        assert reopened.get_version(version.ref) == version
        assert reopened.effective_version("project-1", "variant-1") == version
    finally:
        reopened.close()


def test_cache_gc_does_not_delete_formal_history_or_changelog(tmp_path: Path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        build = _build()
        version = _version(build, "version-1")
        repository.put_build(build)
        repository.publish_version(version, expected_effective_version_id=None)
        document = ChangeLogDocument(
            ChangeLogDocumentRef("document-1", "document-content-1"),
            version.ref,
            "zh-CN",
            "v1",
            "template-1",
            (("summary", ("one",)),),
            (),
        )
        repository.put_changelog(document)
        for kind in CacheKind:
            repository.cache.put(kind, f"{kind.value}-1", b"one")
            repository.cache.put(kind, f"{kind.value}-2", b"two")
            assert repository.cache.gc(kind, max_entries=0) == 2

        assert repository.get_version(version.ref) == version
        assert repository.get_changelog(document.ref) == document
    finally:
        repository.close()


def test_artifact_ledger_allows_status_updates_but_not_identity_changes(tmp_path: Path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        pending = ArtifactLedgerEntry(
            "artifact-1",
            "version-1",
            ArtifactKind.CHANGELOG_MARKDOWN,
            "renderer-1",
            "digest-1",
            "output.md",
        )
        assert repository.put_artifact(pending) == pending
        rendering = replace(pending, status=ArtifactStatus.RENDERING, revision=1)
        assert (
            repository.update_artifact(
                rendering,
                expected_status=ArtifactStatus.PENDING,
                expected_revision=0,
            )
            == rendering
        )
        succeeded = replace(rendering, status=ArtifactStatus.SUCCEEDED, revision=2)
        assert (
            repository.update_artifact(
                succeeded,
                expected_status=ArtifactStatus.RENDERING,
                expected_revision=1,
            )
            == succeeded
        )
        assert repository.artifacts.get("artifact-1") == succeeded
        with pytest.raises(RepositoryConflictError, match="cannot change owner or kind"):
            repository.put_artifact(replace(succeeded, owner_ref="version-2"))
    finally:
        repository.close()


def test_all_paged_facts_and_report_snapshot_round_trip(tmp_path: Path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        first = _candidate(1)
        second = replace(
            _candidate(2),
            original=first.original,
            normalized_original=first.normalized_original,
        )
        evidence = (
            BilingualEvidence(
                "evidence-1",
                "project-1",
                "variant-1",
                ("source-1",),
                "namespace-1",
                "entry-1",
                "Dragon",
                "龙",
                "xml.eet",
                "fingerprint-1",
            ),
        )
        conflict = ConflictGroup(
            "conflict-1",
            "project-1",
            "variant-1",
            first.normalized_original,
            (
                ConflictVariant(first.normalized_translation, (first.candidate_id,), first.evidence_ids),
                ConflictVariant(second.normalized_translation, (second.candidate_id,), second.evidence_ids),
            ),
        )
        build = replace(
            _build(candidates=(first, second)),
            summary=BuildSummary(1, 1, 2, 1),
            evidence=evidence,
            conflicts=(conflict,),
        )
        repository.put_build(build)
        version = _version(build, "version-1")
        repository.publish_version(version, expected_effective_version_id=None)
        action = ManualAction(
            "action-1",
            version.decisions[0].term_id,
            ManualActionType.CHANGE_TRANSLATION,
            "tester",
            "2026-08-28T00:00:00Z",
            version.ref.version_id,
            "before-1",
            "after-1",
        )
        draft = TerminologyDraft(
            DraftRef(
                "draft-1",
                "project-1",
                "variant-1",
                version.ref.version_id,
                version.ref.content_digest,
                0,
                "decisions-1",
            ),
            version.decisions,
            (action,),
        )
        repository.create_draft(draft)
        report = TerminologyReportSnapshot(
            TerminologyReportSnapshotRef("report-1", "report-content-1"),
            build.ref,
            draft.ref,
            None,
            version.decisions,
            (conflict,),
            (action,),
        )
        repository.put_report_snapshot(report)

        assert repository.list_evidence(build.ref).items == evidence
        assert repository.list_conflicts(build.ref).items == (conflict,)
        assert repository.list_terms(version.ref).items == version.decisions
        assert repository.list_manual_actions(draft.ref).items == (action,)
        assert repository.get_report_snapshot(report.ref) == report
    finally:
        repository.close()


def test_version_history_cursor_stales_when_snapshot_changes(tmp_path: Path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        build = _build()
        repository.put_build(build)
        repository.publish_version(_version(build, "version-1"), expected_effective_version_id=None)
        repository.publish_version(
            _version(build, "version-2", "version-1"),
            expected_effective_version_id="version-1",
        )
        first = repository.list_versions("project-1", "variant-1", PageRequest(limit=1))
        assert first.next_cursor is not None
        repository.publish_version(
            _version(build, "version-3", "version-2"),
            expected_effective_version_id="version-2",
        )

        with pytest.raises(CursorStaleError):
            repository.list_versions(
                "project-1",
                "variant-1",
                PageRequest(limit=1, cursor=first.next_cursor),
            )
    finally:
        repository.close()
