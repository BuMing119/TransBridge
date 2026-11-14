# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Optional, exactly admitted cache schema and transactional invalidation fences."""

_FENCE = """CREATE TABLE alignment_cache_fence (
    variant_id TEXT NOT NULL REFERENCES variant(identity) ON DELETE CASCADE,
    entry_id INTEGER NOT NULL REFERENCES source_entry(id) ON DELETE CASCADE,
    nonce TEXT NOT NULL,
    generation INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (variant_id, entry_id)
) STRICT, WITHOUT ROWID"""
_RECORD = """CREATE TABLE alignment_cache_record (
    id INTEGER PRIMARY KEY,
    variant_id TEXT NOT NULL,
    entry_id INTEGER NOT NULL,
    request_key TEXT NOT NULL,
    payload TEXT NOT NULL,
    used INTEGER NOT NULL,
    FOREIGN KEY (variant_id, entry_id) REFERENCES alignment_cache_fence(variant_id, entry_id) ON DELETE CASCADE,
    UNIQUE (variant_id, entry_id, request_key)
) STRICT"""


def _invalidate(where):
    return (
        f"UPDATE alignment_cache_fence SET generation = generation + 1 WHERE {where}; "
        f"DELETE FROM alignment_cache_record WHERE {where};"
    )


def _trigger(name, event, table, when, where):
    return f"CREATE TRIGGER {name} AFTER {event} ON {table} WHEN {when} BEGIN {_invalidate(where)} END"


_DEFAULT_NEW = "COALESCE((SELECT translation FROM entry_default WHERE entry_id = NEW.entry_id), '')"
_DEFAULT_OLD = "COALESCE((SELECT translation FROM entry_default WHERE entry_id = OLD.entry_id), '')"
_TOMBSTONE_NEW = "COALESCE((SELECT tombstone FROM entry_default WHERE entry_id = NEW.entry_id), 0)"
_TOMBSTONE_OLD = "COALESCE((SELECT tombstone FROM entry_default WHERE entry_id = OLD.entry_id), 0)"
_NEW_ROW = "variant_id = NEW.variant_id AND entry_id = NEW.entry_id"
_OLD_ROW = "variant_id = OLD.variant_id AND entry_id = OLD.entry_id"
_DEFAULT_ROWS = (
    "entry_id = {row}.entry_id AND variant_id NOT IN "
    "(SELECT variant_id FROM entry_state WHERE entry_id = {row}.entry_id)"
)

SCHEMA = (
    _FENCE,
    _RECORD,
    "CREATE INDEX alignment_cache_source_fence ON alignment_cache_fence(entry_id)",
    "CREATE INDEX alignment_cache_source_records ON alignment_cache_record(entry_id)",
    "CREATE INDEX alignment_cache_eviction ON alignment_cache_record(variant_id, used, id)",
    "CREATE INDEX alignment_cache_entry_eviction ON alignment_cache_record(variant_id, entry_id, used, id)",
    _trigger(
        "alignment_state_insert",
        "INSERT",
        "entry_state",
        f"NEW.translation IS NOT {_DEFAULT_NEW} OR NEW.tombstone IS NOT {_TOMBSTONE_NEW}",
        _NEW_ROW,
    ),
    _trigger(
        "alignment_state_update",
        "UPDATE OF translation, tombstone",
        "entry_state",
        "NEW.translation IS NOT OLD.translation OR NEW.tombstone IS NOT OLD.tombstone",
        _NEW_ROW,
    ),
    _trigger(
        "alignment_state_delete",
        "DELETE",
        "entry_state",
        f"OLD.translation IS NOT {_DEFAULT_OLD} OR OLD.tombstone IS NOT {_TOMBSTONE_OLD}",
        _OLD_ROW,
    ),
    _trigger(
        "alignment_default_insert",
        "INSERT",
        "entry_default",
        "NEW.translation <> '' OR NEW.tombstone <> 0",
        _DEFAULT_ROWS.format(row="NEW"),
    ),
    _trigger(
        "alignment_default_update",
        "UPDATE OF translation, tombstone",
        "entry_default",
        "NEW.translation IS NOT OLD.translation OR NEW.tombstone IS NOT OLD.tombstone",
        _DEFAULT_ROWS.format(row="NEW"),
    ),
    _trigger(
        "alignment_default_delete",
        "DELETE",
        "entry_default",
        "OLD.translation <> '' OR OLD.tombstone <> 0",
        _DEFAULT_ROWS.format(row="OLD"),
    ),
    _trigger(
        "alignment_source_entry_update",
        "UPDATE OF original, context, metadata, source_id, local_key, identity_original",
        "source_entry",
        "NEW.original IS NOT OLD.original OR NEW.context IS NOT OLD.context OR NEW.metadata IS NOT OLD.metadata "
        "OR NEW.source_id IS NOT OLD.source_id OR NEW.local_key IS NOT OLD.local_key "
        "OR NEW.identity_original IS NOT OLD.identity_original",
        "entry_id = NEW.id",
    ),
    _trigger(
        "alignment_source_unpublish",
        "DELETE",
        "project_source",
        "1",
        "entry_id IN (SELECT id FROM source_entry WHERE source_id = OLD.source_id)",
    ),
    _trigger(
        "alignment_source_scope_update",
        "UPDATE OF namespace",
        "source",
        "NEW.namespace IS NOT OLD.namespace",
        "entry_id IN (SELECT id FROM source_entry WHERE source_id = NEW.id)",
    ),
    _trigger(
        "alignment_published_scope_update",
        "UPDATE OF namespace, source_id",
        "project_source",
        "NEW.namespace IS NOT OLD.namespace OR NEW.source_id IS NOT OLD.source_id",
        "entry_id IN (SELECT id FROM source_entry WHERE source_id IN (OLD.source_id, NEW.source_id))",
    ),
    _trigger("alignment_project_delete", "UPDATE OF deleted", "project", "NEW.deleted <> 0", "1"),
)


def installed(connection):
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'alignment_cache_fence'"
        ).fetchone()
        is not None
    )


def install(connection):
    if not installed(connection):
        for statement in SCHEMA:
            connection.execute(statement)


def remove(connection):
    """Strip cache state from a private export/import copy, never the live source."""
    if not installed(connection):
        return
    for statement in SCHEMA:
        if statement.startswith("CREATE TRIGGER "):
            connection.execute(f"DROP TRIGGER {statement.split()[2]}")
    connection.execute("DROP TABLE alignment_cache_record")
    connection.execute("DROP TABLE alignment_cache_fence")
