# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Closed schema for the translation project database."""

SCHEMA = (
    """CREATE TABLE project (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        format TEXT NOT NULL,
        identity TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 0),
        active_variant TEXT NOT NULL REFERENCES variant(identity) DEFERRABLE INITIALLY DEFERRED,
        settings TEXT NOT NULL DEFAULT '{}',
        deleted INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0, 1))
    ) STRICT""",
    """CREATE TABLE variant (
        identity TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        ordinal INTEGER NOT NULL UNIQUE CHECK (ordinal >= 0),
        revision INTEGER NOT NULL CHECK (revision >= 0),
        label_library TEXT NOT NULL DEFAULT '{}',
        terminology_selection TEXT
    ) STRICT""",
    """CREATE TABLE source (
        id INTEGER PRIMARY KEY,
        namespace TEXT NOT NULL,
        name TEXT NOT NULL,
        format TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        entry_count INTEGER NOT NULL CHECK (entry_count >= 0),
        options TEXT NOT NULL DEFAULT '{}',
        format_fields TEXT NOT NULL DEFAULT '{}'
    ) STRICT""",
    """CREATE TABLE project_source (
        source_id INTEGER PRIMARY KEY REFERENCES source(id),
        namespace TEXT NOT NULL UNIQUE,
        ordinal INTEGER NOT NULL UNIQUE CHECK (ordinal >= 0)
    ) STRICT""",
    """CREATE TABLE source_format_record (
        source_id INTEGER NOT NULL REFERENCES source(id) ON DELETE CASCADE,
        ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
        record TEXT NOT NULL,
        tail TEXT,
        PRIMARY KEY (source_id, ordinal)
    ) STRICT, WITHOUT ROWID""",
    """CREATE TABLE source_entry (
        id INTEGER PRIMARY KEY,
        source_id INTEGER NOT NULL REFERENCES source(id) ON DELETE CASCADE,
        local_key TEXT NOT NULL,
        identity_original TEXT NOT NULL,
        has_identity_original INTEGER NOT NULL CHECK (has_identity_original IN (0, 1)),
        ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
        reading_ordinal INTEGER CHECK (reading_ordinal >= 0),
        legacy_id TEXT NOT NULL,
        original TEXT NOT NULL,
        context TEXT,
        string_id INTEGER,
        metadata TEXT NOT NULL DEFAULT '{}',
        CHECK ((has_identity_original = 0 AND identity_original = '') OR identity_original = original),
        UNIQUE (source_id, local_key, has_identity_original, identity_original),
        UNIQUE (source_id, ordinal)
    ) STRICT""",
    "CREATE UNIQUE INDEX source_reading_order ON source_entry(source_id, reading_ordinal) "
    "WHERE reading_ordinal IS NOT NULL",
    """CREATE TABLE entry_state (
        variant_id TEXT NOT NULL REFERENCES variant(identity) ON DELETE CASCADE,
        entry_id INTEGER NOT NULL REFERENCES source_entry(id) ON DELETE CASCADE,
        translation TEXT NOT NULL,
        stage INTEGER NOT NULL CHECK (stage IN (-1, 0, 1, 2, 3, 5, 9)),
        labels TEXT NOT NULL,
        provenance TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 0),
        tombstone INTEGER NOT NULL CHECK (tombstone IN (0, 1)),
        inferred_fields TEXT NOT NULL,
        external_refs TEXT NOT NULL,
        PRIMARY KEY (variant_id, entry_id)
    ) STRICT, WITHOUT ROWID""",
    """CREATE TABLE named_snapshot (
        identity TEXT PRIMARY KEY,
        variant_id TEXT NOT NULL,
        name TEXT NOT NULL,
        revision INTEGER NOT NULL,
        label_library TEXT NOT NULL,
        source_settings TEXT NOT NULL
    ) STRICT""",
    """CREATE TABLE entry_default (
        entry_id INTEGER PRIMARY KEY REFERENCES source_entry(id) ON DELETE CASCADE,
        translation TEXT NOT NULL,
        stage INTEGER NOT NULL CHECK (stage IN (-1, 0, 1, 2, 3, 5, 9)),
        labels TEXT NOT NULL,
        provenance TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 0),
        tombstone INTEGER NOT NULL CHECK (tombstone IN (0, 1)),
        inferred_fields TEXT NOT NULL,
        external_refs TEXT NOT NULL
    ) STRICT""",
    "CREATE INDEX removed_variant_entries ON entry_state(variant_id, entry_id) WHERE tombstone = 1",
    "CREATE INDEX removed_default_entries ON entry_default(entry_id) WHERE tombstone = 1",
    """CREATE TABLE snapshot_source (
        snapshot_id TEXT NOT NULL REFERENCES named_snapshot(identity) ON DELETE CASCADE,
        source_id INTEGER NOT NULL REFERENCES source(id),
        ordinal INTEGER NOT NULL,
        PRIMARY KEY (snapshot_id, source_id),
        UNIQUE (snapshot_id, ordinal)
    ) STRICT, WITHOUT ROWID""",
    """CREATE TABLE snapshot_entry_state (
        snapshot_id TEXT NOT NULL REFERENCES named_snapshot(identity) ON DELETE CASCADE,
        entry_id INTEGER NOT NULL REFERENCES source_entry(id),
        translation TEXT NOT NULL,
        stage INTEGER NOT NULL,
        labels TEXT NOT NULL,
        provenance TEXT NOT NULL,
        revision INTEGER NOT NULL,
        tombstone INTEGER NOT NULL,
        inferred_fields TEXT NOT NULL,
        external_refs TEXT NOT NULL,
        PRIMARY KEY (snapshot_id, entry_id)
    ) STRICT, WITHOUT ROWID""",
    """CREATE TABLE commit_receipt (
        operation_id TEXT PRIMARY KEY,
        variant_id TEXT NOT NULL,
        expected_project_revision INTEGER NOT NULL,
        expected_variant_revision INTEGER NOT NULL,
        project_revision INTEGER NOT NULL,
        variant_revision INTEGER NOT NULL,
        request TEXT NOT NULL,
        result TEXT NOT NULL
    ) STRICT""",
)
