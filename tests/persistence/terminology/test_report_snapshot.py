from __future__ import annotations

from pathlib import Path

import pytest

from tests.application.terminology.story08_support import build, decision, draft
from transbridge.application.terminology.errors import CursorStaleError, DigestCollisionError
from transbridge.application.terminology.models import DraftRef, TerminologyDraft, TerminologyReportSnapshot
from transbridge.application.terminology.ports import PageRequest
from transbridge.application.terminology.reports import TerminologyReportSnapshotFactory
from transbridge.persistence.terminology import SqliteTerminologyRepository, TerminologyPaths
from transbridge.persistence.terminology.connection import TerminologyConnectionFactory


def _repository(tmp_path: Path) -> SqliteTerminologyRepository:
    return SqliteTerminologyRepository(TerminologyConnectionFactory(TerminologyPaths(tmp_path)), "project-1")


def test_store_persists_immutable_manifest_and_pages_frozen_sections(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    try:
        repository.put_build(build())
        snapshot = TerminologyReportSnapshotFactory(repository).freeze(build().ref, draft=draft())
        assert repository.put_report_snapshot(snapshot) == snapshot.ref
        manifest = repository.get_report_manifest(snapshot.ref)
        page = repository.list_report_terms(snapshot.ref, PageRequest(limit=1))

        assert manifest.snapshot_ref == snapshot.ref
        assert dict(manifest.section_counts) == {"conflicts": 0, "manual": 0, "terms": 1}
        assert page.items == (decision(),)
        assert page.snapshot_digest == snapshot.ref.content_digest
    finally:
        repository.close()


def test_manifest_and_pages_never_deserialize_the_complete_snapshot_payload(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    try:
        repository.put_build(build())
        snapshot = TerminologyReportSnapshotFactory(repository).freeze(build().ref, draft=draft())
        repository.put_report_snapshot(snapshot)
        statements: list[str] = []
        repository._connection.set_trace_callback(statements.append)

        manifest = repository.get_report_manifest(snapshot.ref)
        page = repository.list_report_terms(snapshot.ref, PageRequest(limit=1))

        assert manifest.snapshot_ref == snapshot.ref
        assert page.items == snapshot.terms
        assert not any(
            "payload_json from report_snapshots" in statement.lower().replace("\n", " ") for statement in statements
        )
    finally:
        repository._connection.set_trace_callback(None)
        repository.close()


def test_report_section_cursor_is_stale_for_another_snapshot_digest(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    try:
        repository.put_build(build())
        original_draft = draft()
        extra = decision("term-2", translation="飞龙")
        expanded_draft = type(original_draft)(original_draft.ref, (decision(), extra))
        first = TerminologyReportSnapshotFactory(repository).freeze(build().ref, draft=expanded_draft)
        repository.put_report_snapshot(first)
        first_page = repository.list_report_terms(first.ref, PageRequest(limit=1))
        assert first_page.next_cursor is not None

        other_draft = TerminologyDraft(
            DraftRef("draft-2", "project-1", "variant-1", None, "no-base", 0, "decision-set-2"),
            first.terms,
        )
        other = TerminologyReportSnapshotFactory(repository).freeze(build().ref, draft=other_draft)
        repository.put_report_snapshot(other)
        with pytest.raises(CursorStaleError):
            repository.list_report_terms(other.ref, PageRequest(limit=1, cursor=first_page.next_cursor))
    finally:
        repository.close()


def test_report_snapshot_identity_collision_does_not_replace_existing_facts(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    try:
        repository.put_build(build())
        snapshot = TerminologyReportSnapshotFactory(repository).freeze(build().ref, draft=draft())
        repository.put_report_snapshot(snapshot)
        conflicting = TerminologyReportSnapshot(
            snapshot.ref,
            snapshot.build_ref,
            snapshot.draft_ref,
            None,
            (decision(translation="巨龙"),),
            snapshot.conflicts,
            snapshot.manual_actions,
        )

        with pytest.raises(DigestCollisionError):
            repository.put_report_snapshot(conflicting)

        assert repository.get_report_snapshot(snapshot.ref) == snapshot
    finally:
        repository.close()
