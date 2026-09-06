"""Versioned SQLite schema for project terminology facts."""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 4

DDL = """
CREATE TABLE IF NOT EXISTS schema_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1)
);
CREATE TABLE IF NOT EXISTS migration_history (
    from_version INTEGER NOT NULL,
    to_version INTEGER NOT NULL,
    backup_path TEXT NOT NULL,
    completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (from_version, to_version)
);
CREATE TABLE IF NOT EXISTS migration_history_evidence (
    from_version INTEGER NOT NULL,
    to_version INTEGER NOT NULL,
    source_digest TEXT NOT NULL CHECK(length(source_digest) = 64),
    backup_digest TEXT NOT NULL CHECK(length(backup_digest) = 64),
    backup_path TEXT NOT NULL,
    completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (from_version, to_version)
);
CREATE TABLE IF NOT EXISTS builds (
    build_key TEXT PRIMARY KEY,
    content_digest TEXT NOT NULL,
    project_id TEXT NOT NULL,
    variant_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE (build_key, content_digest)
);
CREATE TABLE IF NOT EXISTS build_evidence (
    build_key TEXT NOT NULL REFERENCES builds(build_key) ON DELETE RESTRICT,
    stable_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (build_key, stable_id)
);
CREATE TABLE IF NOT EXISTS build_candidates (
    build_key TEXT NOT NULL REFERENCES builds(build_key) ON DELETE RESTRICT,
    stable_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (build_key, stable_id)
);
CREATE TABLE IF NOT EXISTS build_conflicts (
    build_key TEXT NOT NULL REFERENCES builds(build_key) ON DELETE RESTRICT,
    stable_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (build_key, stable_id)
);
CREATE TABLE IF NOT EXISTS drafts (
    project_id TEXT NOT NULL,
    variant_id TEXT NOT NULL,
    draft_id TEXT NOT NULL UNIQUE,
    base_version_id TEXT,
    base_content_digest TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    decision_set_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (project_id, variant_id)
);
CREATE TABLE IF NOT EXISTS draft_actions (
    draft_id TEXT NOT NULL REFERENCES drafts(draft_id) ON DELETE CASCADE,
    stable_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (draft_id, stable_id)
);
CREATE TABLE IF NOT EXISTS versions (
    version_key TEXT PRIMARY KEY,
    version_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    variant_id TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    parent_version_key TEXT REFERENCES versions(version_key) ON DELETE RESTRICT,
    build_key TEXT NOT NULL REFERENCES builds(build_key) ON DELETE RESTRICT,
    published_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE (project_id, variant_id, version_id)
);
CREATE TABLE IF NOT EXISTS version_terms (
    version_key TEXT NOT NULL REFERENCES versions(version_key) ON DELETE RESTRICT,
    stable_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (version_key, stable_id)
);
CREATE TABLE IF NOT EXISTS canonical_diffs (
    version_key TEXT PRIMARY KEY REFERENCES versions(version_key) ON DELETE RESTRICT,
    content_digest TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS version_conflicts (
    version_key TEXT NOT NULL REFERENCES versions(version_key) ON DELETE RESTRICT,
    stable_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (version_key, stable_id)
);
CREATE TABLE IF NOT EXISTS version_manual_actions (
    version_key TEXT NOT NULL REFERENCES versions(version_key) ON DELETE RESTRICT,
    stable_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (version_key, stable_id)
);
CREATE TABLE IF NOT EXISTS effective_versions (
    project_id TEXT NOT NULL,
    variant_id TEXT NOT NULL,
    version_key TEXT NOT NULL REFERENCES versions(version_key) ON DELETE RESTRICT,
    PRIMARY KEY (project_id, variant_id)
);
CREATE TABLE IF NOT EXISTS report_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    content_digest TEXT NOT NULL UNIQUE,
    build_key TEXT NOT NULL REFERENCES builds(build_key) ON DELETE RESTRICT,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS report_snapshot_manifests (
    snapshot_id TEXT PRIMARY KEY REFERENCES report_snapshots(snapshot_id) ON DELETE RESTRICT,
    manifest_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS report_snapshot_sections (
    snapshot_id TEXT NOT NULL REFERENCES report_snapshots(snapshot_id) ON DELETE RESTRICT,
    section TEXT NOT NULL,
    stable_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, section, stable_id)
);
CREATE TABLE IF NOT EXISTS changelog_documents (
    document_id TEXT PRIMARY KEY,
    content_digest TEXT NOT NULL UNIQUE,
    version_key TEXT NOT NULL REFERENCES versions(version_key) ON DELETE RESTRICT,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS changelog_manifests (
    document_id TEXT PRIMARY KEY REFERENCES changelog_documents(document_id) ON DELETE RESTRICT,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS changelog_sections (
    document_id TEXT NOT NULL REFERENCES changelog_documents(document_id) ON DELETE RESTRICT,
    section TEXT NOT NULL,
    stable_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (document_id, section, stable_id)
);
CREATE TABLE IF NOT EXISTS artifact_ledger (
    artifact_id TEXT PRIMARY KEY,
    owner_ref TEXT NOT NULL,
    kind TEXT NOT NULL,
    renderer_version TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    target TEXT NOT NULL,
    status TEXT NOT NULL,
    retry_count INTEGER NOT NULL CHECK (retry_count >= 0),
    diagnostic TEXT,
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cache_build (
    cache_key TEXT PRIMARY KEY,
    payload_digest TEXT NOT NULL,
    payload BLOB NOT NULL,
    touched_at INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0)
);
CREATE TABLE IF NOT EXISTS cache_parse (
    cache_key TEXT PRIMARY KEY,
    payload_digest TEXT NOT NULL,
    payload BLOB NOT NULL,
    touched_at INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0)
);
CREATE TABLE IF NOT EXISTS cache_extraction (
    cache_key TEXT PRIMARY KEY,
    payload_digest TEXT NOT NULL,
    payload BLOB NOT NULL,
    touched_at INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0)
);
CREATE TABLE IF NOT EXISTS terminology_sync_lines (
    line_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    variant_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    account_user_id INTEGER CHECK (account_user_id IS NULL OR account_user_id > 0),
    remote_project_id INTEGER NOT NULL CHECK (remote_project_id > 0),
    profile_revision INTEGER NOT NULL CHECK (profile_revision >= 0),
    created_at TEXT NOT NULL,
    retired_at TEXT,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS terminology_sync_profiles (
    line_id TEXT PRIMARY KEY REFERENCES terminology_sync_lines(line_id) ON DELETE RESTRICT,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    mapping_revision INTEGER NOT NULL CHECK (mapping_revision >= 0),
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS terminology_sync_baselines (
    line_id TEXT PRIMARY KEY REFERENCES terminology_sync_lines(line_id) ON DELETE RESTRICT,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    completed_run_id TEXT NOT NULL REFERENCES terminology_sync_runs(run_id) ON DELETE RESTRICT,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS terminology_sync_item_links (
    line_id TEXT NOT NULL REFERENCES terminology_sync_lines(line_id) ON DELETE RESTRICT,
    item_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    remote_id INTEGER CHECK (remote_id IS NULL OR remote_id > 0),
    tombstone TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (line_id, item_id)
);
CREATE TABLE IF NOT EXISTS terminology_sync_runs (
    run_id TEXT PRIMARY KEY,
    line_id TEXT NOT NULL REFERENCES terminology_sync_lines(line_id) ON DELETE RESTRICT,
    plan_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS terminology_sync_outcomes (
    outcome_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES terminology_sync_runs(run_id) ON DELETE RESTRICT,
    line_id TEXT NOT NULL REFERENCES terminology_sync_lines(line_id) ON DELETE RESTRICT,
    item_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS terminology_sync_inbound_sets (
    change_set_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    line_id TEXT NOT NULL REFERENCES terminology_sync_lines(line_id) ON DELETE RESTRICT,
    plan_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (change_set_id, revision)
);
CREATE TABLE IF NOT EXISTS terminology_sync_inbound_items (
    change_set_id TEXT NOT NULL,
    change_set_revision INTEGER NOT NULL CHECK (change_set_revision >= 0),
    item_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (change_set_id, change_set_revision, item_id),
    FOREIGN KEY (change_set_id, change_set_revision)
        REFERENCES terminology_sync_inbound_sets(change_set_id, revision) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS terminology_sync_inbound_reviews (
    change_set_id TEXT NOT NULL,
    change_set_revision INTEGER NOT NULL CHECK (change_set_revision >= 0),
    revision INTEGER NOT NULL CHECK (revision >= 0),
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (change_set_id, revision),
    FOREIGN KEY (change_set_id, change_set_revision)
        REFERENCES terminology_sync_inbound_sets(change_set_id, revision) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS terminology_sync_inbound_dispositions (
    change_set_id TEXT NOT NULL,
    review_revision INTEGER NOT NULL CHECK (review_revision >= 0),
    item_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (change_set_id, review_revision, item_id),
    FOREIGN KEY (change_set_id, review_revision)
        REFERENCES terminology_sync_inbound_reviews(change_set_id, revision) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS terminology_sync_inbound_proposals (
    change_set_id TEXT NOT NULL,
    review_revision INTEGER NOT NULL CHECK (review_revision >= 0),
    proposal_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (change_set_id, review_revision, proposal_digest),
    FOREIGN KEY (change_set_id, review_revision)
        REFERENCES terminology_sync_inbound_reviews(change_set_id, revision) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS terminology_profiles (
    profile_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('active', 'archived')),
    draft_revision INTEGER NOT NULL CHECK (draft_revision >= 0),
    draft_json TEXT NOT NULL,
    latest_published_revision INTEGER CHECK (
        latest_published_revision IS NULL OR latest_published_revision >= 0
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (project_id, profile_id)
);
CREATE TABLE IF NOT EXISTS terminology_profile_revisions (
    profile_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    name TEXT NOT NULL,
    content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
    content_json TEXT NOT NULL,
    published_at TEXT NOT NULL,
    PRIMARY KEY (profile_id, revision),
    UNIQUE (project_id, profile_id, revision),
    FOREIGN KEY (project_id, profile_id)
        REFERENCES terminology_profiles(project_id, profile_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS terminology_profile_selections (
    project_id TEXT NOT NULL,
    variant_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    selected_at TEXT NOT NULL,
    PRIMARY KEY (project_id, variant_id),
    FOREIGN KEY (project_id, profile_id, revision)
        REFERENCES terminology_profile_revisions(project_id, profile_id, revision) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_evidence_page ON build_evidence(build_key, stable_id);
CREATE INDEX IF NOT EXISTS idx_candidate_page ON build_candidates(build_key, stable_id);
CREATE INDEX IF NOT EXISTS idx_conflict_page ON build_conflicts(build_key, stable_id);
CREATE INDEX IF NOT EXISTS idx_action_page ON draft_actions(draft_id, stable_id);
CREATE INDEX IF NOT EXISTS idx_term_page ON version_terms(version_key, stable_id);
CREATE INDEX IF NOT EXISTS idx_version_conflict_page ON version_conflicts(version_key, stable_id);
CREATE INDEX IF NOT EXISTS idx_version_action_page ON version_manual_actions(version_key, stable_id);
CREATE INDEX IF NOT EXISTS idx_version_page ON versions(project_id, variant_id, version_id);
CREATE INDEX IF NOT EXISTS idx_artifact_owner ON artifact_ledger(owner_ref, kind);
CREATE INDEX IF NOT EXISTS idx_report_snapshot_section_page
    ON report_snapshot_sections(snapshot_id, section, stable_id);
CREATE INDEX IF NOT EXISTS idx_changelog_section_page
    ON changelog_sections(document_id, section, stable_id);
CREATE INDEX IF NOT EXISTS idx_cache_build_gc ON cache_build(touched_at, cache_key);
CREATE INDEX IF NOT EXISTS idx_cache_parse_gc ON cache_parse(touched_at, cache_key);
CREATE INDEX IF NOT EXISTS idx_cache_extraction_gc ON cache_extraction(touched_at, cache_key);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sync_active_target
    ON terminology_sync_lines(project_id, target_id) WHERE retired_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_sync_line_variant
    ON terminology_sync_lines(project_id, variant_id, target_id, retired_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sync_live_remote_id
    ON terminology_sync_item_links(line_id, remote_id)
    WHERE remote_id IS NOT NULL AND tombstone = 'live';
CREATE INDEX IF NOT EXISTS idx_sync_item_page ON terminology_sync_item_links(line_id, item_id);
CREATE INDEX IF NOT EXISTS idx_sync_outcome_page ON terminology_sync_outcomes(run_id, outcome_id);
CREATE INDEX IF NOT EXISTS idx_sync_inbound_line
    ON terminology_sync_inbound_sets(line_id, change_set_id, revision);
CREATE INDEX IF NOT EXISTS idx_sync_inbound_review_latest
    ON terminology_sync_inbound_reviews(change_set_id, revision DESC);
CREATE INDEX IF NOT EXISTS idx_terminology_profiles_project
    ON terminology_profiles(project_id, state, name, profile_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_terminology_profiles_name
    ON terminology_profiles(project_id, name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_terminology_profile_selections_profile
    ON terminology_profile_selections(profile_id, project_id, variant_id);
"""

IMMUTABLE_TABLES = (
    "builds",
    "build_evidence",
    "build_candidates",
    "build_conflicts",
    "versions",
    "version_terms",
    "canonical_diffs",
    "version_conflicts",
    "version_manual_actions",
    "report_snapshots",
    "report_snapshot_manifests",
    "report_snapshot_sections",
    "changelog_documents",
    "changelog_manifests",
    "changelog_sections",
    "terminology_sync_runs",
    "terminology_sync_outcomes",
    "terminology_sync_inbound_sets",
    "terminology_sync_inbound_items",
    "terminology_sync_inbound_reviews",
    "terminology_sync_inbound_dispositions",
    "terminology_sync_inbound_proposals",
    "terminology_profile_revisions",
)

REQUIRED_TABLES = frozenset({
    "schema_metadata",
    "migration_history",
    "migration_history_evidence",
    "builds",
    "build_evidence",
    "build_candidates",
    "build_conflicts",
    "drafts",
    "draft_actions",
    "versions",
    "version_terms",
    "canonical_diffs",
    "version_conflicts",
    "version_manual_actions",
    "effective_versions",
    "report_snapshots",
    "report_snapshot_manifests",
    "report_snapshot_sections",
    "changelog_documents",
    "changelog_manifests",
    "changelog_sections",
    "artifact_ledger",
    "cache_build",
    "cache_parse",
    "cache_extraction",
    "terminology_sync_lines",
    "terminology_sync_profiles",
    "terminology_sync_baselines",
    "terminology_sync_item_links",
    "terminology_sync_runs",
    "terminology_sync_outcomes",
    "terminology_sync_inbound_sets",
    "terminology_sync_inbound_items",
    "terminology_sync_inbound_reviews",
    "terminology_sync_inbound_dispositions",
    "terminology_sync_inbound_proposals",
    "terminology_profiles",
    "terminology_profile_revisions",
    "terminology_profile_selections",
})

REQUIRED_SYNC_INDEXES = frozenset({
    "idx_sync_active_target",
    "idx_sync_live_remote_id",
    "idx_sync_item_page",
    "idx_sync_outcome_page",
    "idx_sync_inbound_review_latest",
})

REQUIRED_SYNC_TRIGGERS = frozenset({
    "immutable_terminology_sync_runs_update",
    "immutable_terminology_sync_runs_delete",
    "immutable_terminology_sync_outcomes_update",
    "immutable_terminology_sync_outcomes_delete",
    "immutable_terminology_sync_inbound_sets_update",
    "immutable_terminology_sync_inbound_sets_delete",
    "immutable_terminology_sync_inbound_items_update",
    "immutable_terminology_sync_inbound_items_delete",
    "immutable_terminology_sync_inbound_reviews_update",
    "immutable_terminology_sync_inbound_reviews_delete",
    "immutable_terminology_sync_inbound_dispositions_update",
    "immutable_terminology_sync_inbound_dispositions_delete",
    "immutable_terminology_sync_inbound_proposals_update",
    "immutable_terminology_sync_inbound_proposals_delete",
    "sync_line_identity_immutable",
    "sync_line_no_delete",
    "sync_terminology_sync_profiles_no_delete",
    "sync_terminology_sync_baselines_no_delete",
    "sync_terminology_sync_item_links_no_delete",
})

REQUIRED_PROFILE_TRIGGERS = frozenset({
    "immutable_terminology_profile_revisions_update",
    "immutable_terminology_profile_revisions_delete",
    "terminology_profile_no_delete",
    "terminology_profile_identity_immutable",
})

REQUIRED_PROFILE_INDEXES = frozenset({
    "idx_terminology_profiles_project",
    "idx_terminology_profiles_name",
    "idx_terminology_profile_selections_profile",
})


def initialize_schema(connection: sqlite3.Connection) -> None:
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)
    for table in IMMUTABLE_TABLES:
        connection.execute(
            f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_update BEFORE UPDATE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{table} rows are immutable'); END"
        )
        connection.execute(
            f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_delete BEFORE DELETE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{table} rows are immutable'); END"
        )
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS sync_line_identity_immutable BEFORE UPDATE ON terminology_sync_lines "
        "WHEN OLD.line_id != NEW.line_id OR OLD.project_id != NEW.project_id "
        "OR OLD.variant_id != NEW.variant_id OR OLD.target_id != NEW.target_id "
        "OR OLD.endpoint != NEW.endpoint OR OLD.account_user_id IS NOT NEW.account_user_id "
        "OR OLD.remote_project_id != NEW.remote_project_id OR OLD.profile_revision != NEW.profile_revision "
        "OR OLD.created_at != NEW.created_at "
        "BEGIN SELECT RAISE(ABORT, 'sync line identity is immutable'); END"
    )
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS sync_line_no_delete BEFORE DELETE ON terminology_sync_lines "
        "BEGIN SELECT RAISE(ABORT, 'sync line history is append-only'); END"
    )
    for table in (
        "terminology_sync_profiles",
        "terminology_sync_baselines",
        "terminology_sync_item_links",
    ):
        connection.execute(
            f"CREATE TRIGGER IF NOT EXISTS sync_{table}_no_delete BEFORE DELETE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{table} rows cannot be deleted'); END"
        )
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS terminology_profile_no_delete BEFORE DELETE ON terminology_profiles "
        "BEGIN SELECT RAISE(ABORT, 'terminology profiles cannot be deleted; archive them instead'); END"
    )
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS terminology_profile_identity_immutable "
        "BEFORE UPDATE ON terminology_profiles "
        "WHEN OLD.profile_id != NEW.profile_id OR OLD.project_id != NEW.project_id "
        "OR OLD.created_at != NEW.created_at "
        "BEGIN SELECT RAISE(ABORT, 'terminology profile identity is immutable'); END"
    )
    connection.execute(
        "INSERT INTO schema_metadata(singleton, schema_version) VALUES (1, ?) "
        "ON CONFLICT(singleton) DO UPDATE SET schema_version=excluded.schema_version",
        (SCHEMA_VERSION,),
    )
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def validate_schema(connection: sqlite3.Connection) -> str | None:
    tables = {
        str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    missing = sorted(REQUIRED_TABLES - tables)
    if missing:
        return "missing tables: " + ", ".join(missing)
    indexes = {
        str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
    }
    missing_indexes = sorted((REQUIRED_SYNC_INDEXES | REQUIRED_PROFILE_INDEXES) - indexes)
    if missing_indexes:
        return "missing indexes: " + ", ".join(missing_indexes)
    triggers = {
        str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall()
    }
    missing_triggers = sorted((REQUIRED_SYNC_TRIGGERS | REQUIRED_PROFILE_TRIGGERS) - triggers)
    if missing_triggers:
        return "missing triggers: " + ", ".join(missing_triggers)
    row = connection.execute("SELECT schema_version FROM schema_metadata WHERE singleton = 1").fetchone()
    if row is None or int(row[0]) != SCHEMA_VERSION:
        return "schema metadata does not match user_version"
    return None


__all__ = [
    "DDL",
    "IMMUTABLE_TABLES",
    "REQUIRED_TABLES",
    "REQUIRED_PROFILE_INDEXES",
    "REQUIRED_SYNC_INDEXES",
    "REQUIRED_SYNC_TRIGGERS",
    "REQUIRED_PROFILE_TRIGGERS",
    "SCHEMA_VERSION",
    "initialize_schema",
    "validate_schema",
]
