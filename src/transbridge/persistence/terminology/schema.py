"""Versioned SQLite schema for project terminology facts."""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 2

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
)

REQUIRED_TABLES = frozenset({
    "schema_metadata",
    "migration_history",
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
})


def initialize_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(DDL)
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
    row = connection.execute("SELECT schema_version FROM schema_metadata WHERE singleton = 1").fetchone()
    if row is None or int(row[0]) != SCHEMA_VERSION:
        return "schema metadata does not match user_version"
    return None


__all__ = [
    "DDL",
    "IMMUTABLE_TABLES",
    "REQUIRED_TABLES",
    "SCHEMA_VERSION",
    "initialize_schema",
    "validate_schema",
]
