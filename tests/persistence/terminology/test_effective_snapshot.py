from __future__ import annotations

import sqlite3

from tests.application.terminology.story08_support import Permit, State, build, draft, expected
from transbridge.ai_translator.project_terminology_adapter import (
    ProjectTerminologyAdapter,
    PublishedEffectiveTerminologyGate,
)
from transbridge.ai_translator.term_formats import TermEntry
from transbridge.application.terminology.effective import (
    EffectiveSnapshotStatus,
    SnapshotEffectiveTerminologyPort,
    TerminologyLookupContext,
)
from transbridge.application.terminology.publish import PublishTerminologyRequest, VersionPublisher
from transbridge.persistence.terminology import (
    SqliteEffectiveTerminologySnapshotPort,
    SqliteTerminologyRepository,
)


def _publish(repository: SqliteTerminologyRepository) -> None:
    source = build()
    reviewed = draft()
    repository.put_build(source)
    repository.create_draft(reviewed)
    request = PublishTerminologyRequest(
        project_id="project-1",
        variant_id="variant-1",
        expected=expected(),
        build_ref=source.ref,
        draft_ref=reviewed.ref,
        version_id="version-1",
        published_at="2026-08-28T01:00:00+00:00",
    )
    VersionPublisher(repository.publisher, State(request.expected), Permit()).publish(request)


def test_sqlite_snapshot_reads_the_current_published_membership(tmp_path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    _publish(repository)

    snapshot = SqliteEffectiveTerminologySnapshotPort(repository).snapshot("project-1", "variant-1")

    assert snapshot.status is EffectiveSnapshotStatus.READY
    assert snapshot.version_id == "version-1"
    assert [(item.original, item.translation) for item in snapshot.decisions] == [("Dragon", "龙")]


def test_read_only_repository_preserves_legacy_path_instead_of_consuming_assets(tmp_path) -> None:
    writable = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    _publish(writable)
    writable.close()
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1", writable=False)

    snapshot = SqliteEffectiveTerminologySnapshotPort(repository).snapshot("project-1", "variant-1")
    adapter = ProjectTerminologyAdapter(
        SnapshotEffectiveTerminologyPort(SqliteEffectiveTerminologySnapshotPort(repository)),
        PublishedEffectiveTerminologyGate(lambda _project, _variant: True),
    )
    legacy = (TermEntry("Dragon", "旧龙", "dynamic"),)
    loaded = adapter.load(TerminologyLookupContext("project-1", "variant-1"), legacy)

    assert snapshot.status is EffectiveSnapshotStatus.UNAVAILABLE
    assert "read-only" in snapshot.diagnostics[0]
    assert loaded.entries == legacy
    assert loaded.status is EffectiveSnapshotStatus.UNAVAILABLE


def test_digest_mismatch_is_reported_as_corrupt_and_never_exposes_decisions(tmp_path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    _publish(repository)
    repository._connection.execute("DROP TRIGGER immutable_versions_update")
    repository._connection.execute("UPDATE versions SET content_digest = ?", ("tampered",))
    repository._connection.commit()

    snapshot = SqliteEffectiveTerminologySnapshotPort(repository).snapshot("project-1", "variant-1")

    assert snapshot.status is EffectiveSnapshotStatus.CORRUPT
    assert snapshot.decisions == ()
    assert "digest" in snapshot.diagnostics[0]


def test_invalid_payload_is_contained_as_corrupt_snapshot(tmp_path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    _publish(repository)
    repository._connection.execute("DROP TRIGGER immutable_versions_update")
    repository._connection.execute("UPDATE versions SET payload_json = ?", ("{invalid",))
    repository._connection.commit()

    snapshot = SqliteEffectiveTerminologySnapshotPort(repository).snapshot("project-1", "variant-1")

    assert snapshot.status is EffectiveSnapshotStatus.CORRUPT
    assert snapshot.decisions == ()
    assert repository.storage_state.integrity_ok is False


def test_other_project_identity_is_rejected_before_any_sqlite_lookup(tmp_path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")

    try:
        SqliteEffectiveTerminologySnapshotPort(repository).snapshot("project-2", "variant-1")
    except ValueError as exc:
        assert "another Project" in str(exc)
    else:
        raise AssertionError("cross-project effective lookup was accepted")


def test_unpublished_variant_does_not_consume_another_variants_version(tmp_path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    _publish(repository)

    snapshot = SqliteEffectiveTerminologySnapshotPort(repository).snapshot("project-1", "variant-2")

    assert snapshot.status is EffectiveSnapshotStatus.NO_PROJECT_VERSION
    assert snapshot.decisions == ()


def test_sqlite_integrity_failure_is_contained_if_it_occurs_during_lookup(tmp_path, monkeypatch) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")

    def fail(*_args, **_kwargs):
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(repository, "effective_version", fail)

    snapshot = SqliteEffectiveTerminologySnapshotPort(repository).snapshot("project-1", "variant-1")

    assert snapshot.status is EffectiveSnapshotStatus.CORRUPT
    assert "read safely" in snapshot.diagnostics[0]
