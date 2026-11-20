from dataclasses import replace

import pytest

from tests.application.test_record_fields import entry
from transbridge.application.dialogue.record_fields import find_record_entries, record_identity
from transbridge.application.io.contracts import FormatId
from transbridge.application.io.identity import SourceNamespace
from transbridge.application.projects.storage_contracts import ExtractedSource, StoredEntry
from transbridge.persistence.project_store.connection import project_connection
from transbridge.persistence.project_store.format import ProjectStorageConflict
from transbridge.persistence.project_store.paged_reads import ProjectPagedRead, WorkbenchSource
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.persistence.v2.ids import ProjectId, VariantId
from transbridge.persistence.v2.variant import VariantEntryState
from transbridge.ui.project_rows import ProjectRowsCollection


def project(tmp_path, *, quest_fields=0):
    rows = [entry(f"Filler{index}:0000{index + 100:04X}|1~BOOK:FULL") for index in range(110)]
    rows += [
        entry("Book:00000001|1~BOOK:FULL"),
        entry("None:00000001|1~BOOK:DESC"),
        entry("Different:00000001|2~BOOK:DESC"),
        entry("None:00000001|1~NPC_:FULL"),
        entry(
            "opaque-key",
            metadata=(("plugin.record_type", "BOOK"), ("plugin.form_id", "00000001"), ("plugin.field_type", "DESC")),
        ),
    ]
    rows += [entry(f"Quest|Editor:00000003|{index}~QUST:NNAM") for index in range(quest_fields)]
    stored = tuple(StoredEntry(item.snapshot(), VariantEntryState(item.identity)) for item in rows)
    stored = (*stored[:112], replace(stored[112], state=replace(stored[112].state, tombstone=True)), *stored[113:])
    source = ExtractedSource(SourceNamespace("source:a"), "Books.esp", FormatId.PLUGIN_SSE, "a" * 64, stored)
    store = ProjectStore(tmp_path / "project.sqlite3")
    variant = VariantId("variant")
    store.create(ProjectId("project"), "Project", variant, "Default", (source,))
    token = store.read_token(variant)
    workbench_source = WorkbenchSource("books", source.info, stored[:100], len(stored))
    reader = ProjectPagedRead(store, token, (workbench_source,))
    return store, ProjectRowsCollection(reader, workbench_source), rows


@pytest.mark.parametrize("reopened", [False, True])
def test_paged_record_fields_include_unloaded_fields_and_overlay_restored_tombstones(tmp_path, monkeypatch, reopened):
    store, collection, rows = project(tmp_path)
    if reopened:
        store = ProjectStore(store.path)
        collection.reader.store = store
    selected = rows[110]
    collection._authority = collection.reader.authority().patched((
        VariantEntryState(rows[111].identity, "Updated translation", 1),
        VariantEntryState(rows[112].identity, "Restored field", 1),
        VariantEntryState(rows[114].identity, tombstone=True),
    ))
    monkeypatch.setattr(
        store, "iter_entries", lambda *_args, **_kwargs: pytest.fail("record lookup scanned project rows")
    )
    monkeypatch.setattr(
        store, "read_page", lambda *_args, **_kwargs: pytest.fail("record lookup paged the entire source")
    )
    results = find_record_entries(collection, selected)
    assert [item.identity for item in results] == [item.identity for item in rows[110:113]]
    assert [item.translation for item in results] == ["", "Updated translation", "Restored field"]
    cached = next(iter(store._record_key_cache.values()))
    assert find_record_entries(collection, selected) == results
    assert next(iter(store._record_key_cache.values())) is cached
    with project_connection(store.path) as connection:
        assert (
            connection.execute("SELECT 1 FROM sqlite_schema WHERE name = 'source_record_identity'").fetchone() is None
        )


def test_record_cache_rebuilds_on_revision_and_checks_stale_tokens(tmp_path):
    store, collection, rows = project(tmp_path)
    identity = record_identity(rows[110])
    assert len(store.read_record_entries(collection.token, identity)) == 4
    previous = next(iter(store._record_key_cache.values()))
    with project_connection(store.path, write=True) as connection:
        connection.execute("UPDATE project SET revision = revision + 1")
        connection.execute("UPDATE source_entry SET context = 'NPC_:DESC' WHERE local_key = ?", (rows[111].key,))
    token = store.read_token(collection.token.variant_id)
    assert len(store.read_record_entries(token, identity)) == 3
    assert len(store._record_key_cache) == 1
    assert next(iter(store._record_key_cache.values())) is not previous
    with pytest.raises(ProjectStorageConflict):
        store.read_record_entries(collection.token, identity)


def test_large_record_uses_read_only_directory_once_then_chunked_indexed_queries(tmp_path, monkeypatch):
    from contextlib import contextmanager

    import transbridge.persistence.project_store.repository as repository

    store, collection, rows = project(tmp_path, quest_fields=450)
    identity = record_identity(rows[-1])
    statements = []

    @contextmanager
    def read_only_connection(path, **options):
        assert not options.get("write"), "record browsing must support read-only project databases"
        with project_connection(path, **options) as connection:
            connection.set_trace_callback(statements.append)
            yield connection

    monkeypatch.setattr(repository, "project_connection", read_only_connection)
    first = store.read_record_entries(collection.token, identity)
    assert [row.snapshot.entry_key for row in first] == [row.identity for row in rows[-450:]]
    assert len([sql for sql in statements if "WITH requested" in sql]) == 3
    assert len([sql for sql in statements if "context, metadata" in sql]) == 1
    statements.clear()
    assert store.read_record_entries(collection.token, identity) == first
    assert len([sql for sql in statements if "WITH requested" in sql]) == 3
    assert not any("context, metadata" in sql for sql in statements)
