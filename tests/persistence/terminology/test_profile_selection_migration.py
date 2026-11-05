import sqlite3

import pytest

from tests.persistence.terminology.test_profiles import _profile, _published
from transbridge.application.terminology_profiles import TerminologyProfileSelection
from transbridge.persistence.terminology import (
    TerminologyConnectionFactory,
    TerminologyPaths,
    migration as migration_module,
)
from transbridge.persistence.terminology.migration import TerminologyMigrationError, TerminologyMigrator
from transbridge.persistence.terminology.profiles import SqliteTerminologyProfileRepository


def _legacy(paths):
    opened = TerminologyConnectionFactory(paths).open("project-1")
    connection = opened.connection
    repository = SqliteTerminologyProfileRepository(connection)
    profile = _profile()
    repository.insert_profile(profile)
    repository.insert_published(_published(profile), expected_draft_revision=0)
    selection = TerminologyProfileSelection("project-1", "variant-a", profile.profile_id, 1, "now")
    repository.set_selection(selection)
    connection.execute("UPDATE schema_metadata SET schema_version = 4")
    connection.execute("PRAGMA user_version = 4")
    return connection, selection


def test_old_preview_preference_is_backed_up_and_cleared_once_without_touching_body(tmp_path):
    paths = TerminologyPaths(tmp_path.resolve())
    connection, selection = _legacy(paths)
    connection.close()
    body = tmp_path / "existing-variant.json"
    body.write_bytes(b'{"translation":"unchanged A"}')
    before = body.read_bytes()
    opened = TerminologyConnectionFactory(paths).open("project-1")
    repository = SqliteTerminologyProfileRepository(opened.connection)
    assert repository.get_selection("project-1", "variant-a") is None
    assert repository.get_published(selection.profile_id, 1) is not None
    evidence = opened.connection.execute(
        "SELECT backup_path FROM migration_history_evidence WHERE from_version = 4 AND to_version = 5"
    ).fetchone()
    assert evidence is not None
    with sqlite3.connect(evidence[0]) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 4
        assert (
            backup.execute("SELECT profile_id FROM terminology_profile_selections").fetchone()[0]
            == selection.profile_id
        )
    repository.set_selection(selection)
    opened.connection.close()
    reopened = TerminologyConnectionFactory(paths).open("project-1")
    try:
        assert (
            SqliteTerminologyProfileRepository(reopened.connection).get_selection("project-1", "variant-a") == selection
        )
        assert (
            reopened.connection.execute("SELECT count(*) FROM migration_history WHERE from_version = 4").fetchone()[0]
            == 1
        )
        assert body.read_bytes() == before
    finally:
        reopened.connection.close()


def test_failed_semantic_migration_restores_old_preference_and_schema(tmp_path, monkeypatch):
    paths = TerminologyPaths(tmp_path.resolve())
    connection, selection = _legacy(paths)
    monkeypatch.setattr(migration_module, "validate_schema", lambda _: "injected failure")
    try:
        with pytest.raises(TerminologyMigrationError, match="injected failure"):
            TerminologyMigrator(paths).migrate(connection, "project-1", 4)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert SqliteTerminologyProfileRepository(connection).get_selection("project-1", "variant-a") == selection
    finally:
        connection.close()
