"""Independent task format and ownership indexes."""

APPLICATION_ID = 0x5442544B  # TBTK
VERSION = 1

SCHEMA = (
    """CREATE TABLE run (
        run_id TEXT PRIMARY KEY,
        owner_id TEXT,
        session_id TEXT,
        entrypoint TEXT,
        project_id TEXT,
        variant_id TEXT,
        created_at TEXT,
        state TEXT,
        recovery TEXT,
        report TEXT,
        report_summary TEXT,
        report_revision INTEGER NOT NULL DEFAULT 0,
        terminal TEXT,
        deleted INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0, 1)),
        cleanup_pending INTEGER NOT NULL DEFAULT 0 CHECK (cleanup_pending IN (0, 1))
    ) STRICT""",
    "CREATE INDEX run_recent ON run (created_at DESC, run_id DESC) WHERE deleted = 0",
    "CREATE INDEX run_owner ON run (owner_id, session_id, created_at DESC) WHERE deleted = 0",
    "CREATE INDEX run_target ON run (project_id, variant_id, created_at DESC) WHERE deleted = 0",
    """CREATE TABLE run_field (
        run_id TEXT NOT NULL REFERENCES run ON DELETE CASCADE,
        name TEXT NOT NULL,
        value TEXT NOT NULL,
        PRIMARY KEY (run_id, name)
    ) STRICT""",
    """CREATE TABLE candidate (
        run_id TEXT NOT NULL REFERENCES run ON DELETE CASCADE,
        entry_key TEXT NOT NULL,
        revision INTEGER NOT NULL,
        content TEXT NOT NULL,
        PRIMARY KEY (run_id, entry_key, revision)
    ) STRICT""",
    """CREATE TABLE candidate_head (
        run_id TEXT NOT NULL,
        entry_key TEXT NOT NULL,
        revision INTEGER NOT NULL,
        decision TEXT NOT NULL,
        PRIMARY KEY (run_id, entry_key),
        FOREIGN KEY (run_id, entry_key, revision) REFERENCES candidate ON DELETE CASCADE
    ) STRICT""",
    """CREATE TABLE application (
        operation_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES run ON DELETE CASCADE,
        request TEXT NOT NULL,
        state TEXT NOT NULL,
        receipt TEXT
    ) STRICT""",
    "CREATE INDEX application_run ON application (run_id)",
    """CREATE TABLE report_dependency (
        report_run_id TEXT NOT NULL REFERENCES run ON DELETE CASCADE,
        candidate_run_id TEXT NOT NULL REFERENCES run ON DELETE CASCADE,
        PRIMARY KEY (report_run_id, candidate_run_id)
    ) STRICT""",
    "CREATE INDEX report_candidate_run ON report_dependency (candidate_run_id)",
)
