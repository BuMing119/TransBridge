from dataclasses import replace

import pytest

from tests.application.terminology.test_extraction import _evidence
from tests.contracts.terminology.test_conflict_queries import _conflicts_build
from tests.contracts.terminology.test_repository_contract import _draft, _version
from transbridge.application.terminology.conflict_queries import ConflictFilter
from transbridge.application.terminology.errors import CursorStaleError, RepositoryConflictError
from transbridge.application.terminology.models import BuildResult, TerminologyDraft, TerminologyVersion
from transbridge.application.terminology.query_control import QueryCancellation, QueryCancelled
from transbridge.bootstrap.terminology_workloads import ProductionTerminologyCommands
from transbridge.persistence.terminology import SqliteTerminologyRepository


def test_page_details_and_counts_do_not_decode_complete_snapshots(tmp_path, monkeypatch):
    repository = SqliteTerminologyRepository.open(tmp_path, "project-1")
    evidence = tuple(
        replace(_evidence(f"evidence-{i}", "Dragon", "龙", "BOOK:DESC"), variant_id="variant-1") for i in range(1, 100)
    )
    build = replace(_conflicts_build(), evidence=evidence)
    try:
        repository.put_build(build)
        repository.create_draft(_draft())
        monkeypatch.setattr(repository, "latest_build", lambda *_: pytest.fail("full build read for task reference"))
        assert ProductionTerminologyCommands._latest_build_ref(repository, "project-1", "variant-1") == build.ref
        decode = repository._decode
        decoded_evidence = []

        def bounded_decode(payload, kind):
            assert kind not in (BuildResult, TerminologyDraft, TerminologyVersion)
            value = decode(payload, kind)
            if hasattr(value, "evidence_id"):
                decoded_evidence.append(value.evidence_id)
            return value

        monkeypatch.setattr(repository, "_decode", bounded_decode)
        assert repository.latest_build_ref("project-1", "variant-1") == build.ref
        assert repository.list_review_conflicts(build.ref).total == 4
        group = build.conflicts[0]
        details = repository.conflict_reads.details(group, "project-1", "variant-1")
        assert {item.evidence_id for item in details.evidence} == {"evidence-1", "evidence-2"}
        assert set(decoded_evidence) == {"evidence-1", "evidence-2"}
        counts = repository.conflict_reads.usage_counts(build.ref, (group,))
        assert all(value == 1 for value in counts.values())
        assert len(counts) == 2
        with pytest.raises(PermissionError):
            repository.conflict_reads.details(group, "project-1", "other")
    finally:
        repository.close()


def test_conflict_reads_reject_replaced_build_from_other_connection(tmp_path):
    repository = SqliteTerminologyRepository.open(tmp_path, "project-1")
    other = SqliteTerminologyRepository.open(tmp_path, "project-1")
    try:
        build = _conflicts_build()
        repository.put_build(build)
        repository.create_draft(_draft())
        other.put_build(
            replace(build, ref=replace(build.ref, build_key="new", content_digest="new-content"), conflicts=())
        )
        with pytest.raises(CursorStaleError):
            repository.list_review_conflicts(build.ref)
        with pytest.raises(RepositoryConflictError):
            repository.conflict_reads.details(build.conflicts[0], "project-1", "variant-1")
    finally:
        other.close()
        repository.close()


def test_legacy_published_conflict_overlay_without_materialized_rows(tmp_path):
    repository = SqliteTerminologyRepository.open(tmp_path, "project-1")
    try:
        build = _conflicts_build()
        repository.put_build(build)
        resolved = replace(build.conflicts[0], status="unified", recommended_translation="街道")
        version = replace(_version(build, "v1"), conflicts=(resolved,))
        repository.publish_version(version, expected_effective_version_id=None)
        assert repository._connection.execute("SELECT COUNT(*) FROM version_conflicts").fetchone()[0] == 0
        page = repository.list_review_conflicts(build.ref)
        assert page.items[0] == resolved
        pending = repository.list_review_conflicts(build.ref, filters=ConflictFilter(review_state="pending"))
        assert pending.total == 3
        assert resolved.conflict_group_id not in {item.conflict_group_id for item in pending.items}
        reviewed = repository.list_review_conflicts(build.ref, filters=ConflictFilter(review_state="resolved"))
        assert reviewed.items == (resolved,)
    finally:
        repository.close()


def test_review_details_honor_cancelled_read_and_can_retry(tmp_path):
    repository = SqliteTerminologyRepository.open(tmp_path, "project-1")
    try:
        build = _conflicts_build()
        repository.put_build(build)
        repository.create_draft(_draft())
        cancellation = QueryCancellation()
        cancellation.cancel()
        with pytest.raises(QueryCancelled):
            repository.conflict_reads.details(build.conflicts[0], "project-1", "variant-1", cancellation=cancellation)
        assert (
            repository.conflict_reads.details(build.conflicts[0], "project-1", "variant-1").conflict
            == build.conflicts[0]
        )
    finally:
        repository.close()


def test_manually_overridden_equivalence_is_in_resolved_filter(tmp_path):
    repository = SqliteTerminologyRepository.open(tmp_path, "project-1")
    try:
        build = _conflicts_build()
        equivalent = replace(build.conflicts[0], status="equivalent", recommended_translation="街道", notes="同一术语")
        build = replace(build, conflicts=(equivalent, *build.conflicts[1:]))
        repository.put_build(build)
        manual = replace(equivalent, status="unified", recommended_translation="新译名")
        repository.create_draft(replace(_draft(), conflict_resolutions=(manual,)))
        resolved = repository.list_review_conflicts(
            build.ref, filters=ConflictFilter(include_equivalent=False, review_state="resolved")
        )
        assert resolved.items == (manual,)
        assert repository.list_review_conflicts(build.ref, filters=ConflictFilter(review_state="equivalent")).total == 0
    finally:
        repository.close()
