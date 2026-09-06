from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest

from transbridge.application.terminology.errors import RepositoryConflictError, RevisionConflictError
from transbridge.application.terminology_sync.identity import sync_item_id, sync_line_id
from transbridge.application.terminology_sync.models import (
    TerminologySyncBaseline,
    TerminologySyncCommit,
    TerminologySyncItemLink,
    TerminologySyncItemLinkUpdate,
    TerminologySyncItemOutcomeRecord,
    TerminologySyncLine,
    TerminologySyncOutcome,
    TerminologySyncOwnership,
    TerminologySyncProfile,
    TerminologySyncRunOutcome,
    TerminologySyncRunRecord,
    TerminologySyncTarget,
)
from transbridge.persistence.terminology import (
    SqliteTerminologyRepository,
    TerminologyConnectionFactory,
    TerminologyPaths,
    migration as terminology_migration,
)
from transbridge.persistence.terminology.migration import TerminologyMigrationError, TerminologyMigrator
from transbridge.persistence.terminology.schema import SCHEMA_VERSION
from transbridge.persistence.terminology.sync_codec import loads_sync
from transbridge.persistence.terminology.sync_state import VARIANT_MAPPING_CONFLICT

NOW = "2026-08-30T00:00:00Z"
LATER = "2026-08-30T01:00:00Z"


def _line(variant_id: str = "variant-1", *, profile_revision: int = 0, mapping_revision: int = 0):
    target = TerminologySyncTarget("https://example.com/api", 7, 11)
    line_id = sync_line_id(
        project_id="project-1",
        variant_id=variant_id,
        target_identity=target.target_id,
        profile_revision=profile_revision,
    )
    return (
        TerminologySyncLine(line_id, "project-1", variant_id, target, profile_revision, NOW),
        TerminologySyncProfile(line_id, profile_revision, mapping_revision=mapping_revision),
    )


def _first_commit(line: TerminologySyncLine, *, run_id: str = "run-1") -> TerminologySyncCommit:
    item_id = sync_item_id(line_id=line.line_id, local_term_id="term-1")
    run = TerminologySyncRunRecord(
        run_id,
        line.line_id,
        "plan-1",
        "owner-1",
        line.target.target_id,
        None,
        TerminologySyncRunOutcome.SUCCEEDED,
        NOW,
        LATER,
    )
    outcome = TerminologySyncItemOutcomeRecord(
        "outcome-1",
        run_id,
        line.line_id,
        item_id,
        TerminologySyncOutcome.CONFIRMED,
        "confirmed",
        "confirmed by fresh observation",
        LATER,
    )
    link = TerminologySyncItemLink(
        line.line_id,
        item_id,
        0,
        "term-1",
        "version-1",
        "local-1",
        17,
        None,
        "remote-1",
        "common-1",
        "project",
        TerminologySyncOwnership.MANAGED,
        last_outcome=TerminologySyncOutcome.CONFIRMED,
    )
    baseline = TerminologySyncBaseline(
        line.line_id,
        0,
        "version-1",
        "local-1",
        "remote-snapshot-1",
        "common-snapshot-1",
        run_id,
    )
    return TerminologySyncCommit(run, (outcome,), baseline, (TerminologySyncItemLinkUpdate(link, None),))


def test_first_read_is_distinct_from_unavailable_and_round_trips_transaction(tmp_path: Path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    line, profile = _line()
    try:
        empty = repository.sync_state.resolve_line("project-1", "variant-1", line.target)
        assert empty.line is None and empty.baseline is None and empty.writable and empty.diagnostic is None

        activated = repository.sync_state.activate_line(line, profile)
        assert activated.line == line and activated.profile == profile and activated.baseline is None
        commit = _first_commit(line)
        assert repository.sync_state.commit_run(commit, expected_baseline_revision=None) == commit.baseline
        assert repository.sync_state.get_baseline(line.line_id) == commit.baseline
        assert repository.sync_state.list_item_links(line.line_id).items == (commit.item_links[0].link,)
        assert repository.sync_state.list_outcomes(commit.run.run_id).items == commit.outcomes
    finally:
        repository.close()

    reopened = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        assert reopened.sync_state.resolve_line("project-1", "variant-1", line.target).baseline == commit.baseline
    finally:
        reopened.close()


def test_baseline_and_item_link_cas_failure_rolls_back_entire_run(tmp_path: Path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    line, profile = _line()
    try:
        repository.sync_state.activate_line(line, profile)
        first = _first_commit(line)
        repository.sync_state.commit_run(first, expected_baseline_revision=None)
        bad = replace(
            first,
            run=replace(first.run, run_id="run-2", baseline_revision=0),
            outcomes=(replace(first.outcomes[0], outcome_id="outcome-2", run_id="run-2"),),
            baseline=replace(first.baseline, revision=1, completed_run_id="run-2"),
            item_links=(TerminologySyncItemLinkUpdate(replace(first.item_links[0].link, revision=1), None),),
        )

        with pytest.raises(RevisionConflictError):
            repository.sync_state.commit_run(bad, expected_baseline_revision=0)

        assert repository.sync_state.get_baseline(line.line_id) == first.baseline
        assert repository.sync_state.list_outcomes("run-2").items == ()
    finally:
        repository.close()


def test_unknown_outcome_cannot_advance_common_digest(tmp_path: Path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    line, profile = _line()
    try:
        repository.sync_state.activate_line(line, profile)
        first = _first_commit(line)
        repository.sync_state.commit_run(first, expected_baseline_revision=None)
        unknown_link = replace(
            first.item_links[0].link,
            revision=1,
            common_content_digest="unconfirmed-change",
            last_outcome=TerminologySyncOutcome.UNKNOWN,
        )
        second = TerminologySyncCommit(
            replace(first.run, run_id="run-2", baseline_revision=0, outcome=TerminologySyncRunOutcome.UNKNOWN),
            (
                replace(
                    first.outcomes[0], outcome_id="outcome-2", run_id="run-2", status=TerminologySyncOutcome.UNKNOWN
                ),
            ),
            replace(first.baseline, revision=1, completed_run_id="run-2"),
            (TerminologySyncItemLinkUpdate(unknown_link, 0),),
        )

        with pytest.raises(RepositoryConflictError, match="unconfirmed outcome"):
            repository.sync_state.commit_run(second, expected_baseline_revision=0)

        assert repository.sync_state.list_outcomes("run-2").items == ()
    finally:
        repository.close()


def test_variant_mapping_conflict_and_explicit_replacement_preserve_old_history(tmp_path: Path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    first_line, first_profile = _line()
    replacement, replacement_profile = _line("variant-2", profile_revision=1, mapping_revision=1)
    try:
        repository.sync_state.activate_line(first_line, first_profile)
        first_commit = _first_commit(first_line)
        repository.sync_state.commit_run(first_commit, expected_baseline_revision=None)

        conflict = repository.sync_state.resolve_line("project-1", "variant-2", first_line.target)
        assert conflict.line == first_line
        assert not conflict.writable and conflict.diagnostic == VARIANT_MAPPING_CONFLICT
        activated_conflict = repository.sync_state.activate_line(replacement, replacement_profile)
        assert activated_conflict.diagnostic == VARIANT_MAPPING_CONFLICT

        current = repository.sync_state.replace_active_variant_mapping(
            replacement,
            replacement_profile,
            expected_mapping_revision=0,
            retired_at=LATER,
        )
        assert current.line == replacement and current.writable
        assert repository.sync_state.get_baseline(first_line.line_id) == first_commit.baseline
        assert repository.sync_state.list_item_links(first_line.line_id).items == (first_commit.item_links[0].link,)
    finally:
        repository.close()


def test_v2_database_migrates_with_backup_and_sync_tables(tmp_path: Path) -> None:
    paths = TerminologyPaths(tmp_path.resolve())
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    database = repository.path
    repository.close()
    legacy = sqlite3.connect(database)
    try:
        for table in (
            "terminology_sync_inbound_proposals",
            "terminology_sync_inbound_dispositions",
            "terminology_sync_inbound_reviews",
            "terminology_sync_inbound_items",
            "terminology_sync_inbound_sets",
            "terminology_sync_outcomes",
            "terminology_sync_runs",
            "terminology_sync_item_links",
            "terminology_sync_baselines",
            "terminology_sync_profiles",
            "terminology_sync_lines",
        ):
            legacy.execute(f"DROP TABLE {table}")
        legacy.execute("UPDATE schema_metadata SET schema_version = 2 WHERE singleton = 1")
        legacy.execute("PRAGMA user_version = 2")
        legacy.commit()
    finally:
        legacy.close()

    opened = TerminologyConnectionFactory(paths).open("project-1")
    try:
        assert opened.state.schema_version == SCHEMA_VERSION == 4
        for expected in (
            "terminology_sync_lines",
            "terminology_sync_inbound_reviews",
            "terminology_sync_inbound_dispositions",
            "terminology_sync_inbound_proposals",
            "immutable_terminology_sync_inbound_reviews_update",
        ):
            assert opened.connection.execute(
                "SELECT name FROM sqlite_master WHERE name = ?",
                (expected,),
            ).fetchone()
    finally:
        opened.connection.close()
    assert tuple(paths.backup_directory("project-1").glob("schema-v2-to-v4-*.sqlite3"))


def test_future_sync_payload_version_fails_closed() -> None:
    with pytest.raises(ValueError, match="unsupported sync payload schema"):
        loads_sync('{"schema_version":999,"value":{}}', TerminologySyncBaseline)


def test_schema_validation_fault_rolls_back_authoritative_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = TerminologyPaths(tmp_path.resolve())
    database = paths.database("project-1")
    database.parent.mkdir(parents=True)
    legacy = sqlite3.connect(database, isolation_level=None)
    legacy.execute("CREATE TABLE legacy_sentinel(value TEXT NOT NULL)")
    legacy.execute("INSERT INTO legacy_sentinel(value) VALUES ('preserved')")
    monkeypatch.setattr(terminology_migration, "validate_schema", lambda _connection: "injected invalid schema")

    with pytest.raises(TerminologyMigrationError, match="validation failed"):
        TerminologyMigrator(paths).migrate(legacy, "project-1", 0)
    legacy.close()

    check = sqlite3.connect(database)
    try:
        assert check.execute("PRAGMA user_version").fetchone()[0] == 0
        assert check.execute("SELECT value FROM legacy_sentinel").fetchone()[0] == "preserved"
        assert check.execute("SELECT name FROM sqlite_master WHERE name = 'terminology_sync_lines'").fetchone() is None
    finally:
        check.close()
