from dataclasses import replace
import json
import sqlite3
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from tests.persistence.project_store.test_entries import _stored_entry
from tests.persistence.project_store.test_source_extraction import _create_project
from tests.plugin_fixtures import write_plugin
from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId, Stage
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projects.storage_contracts import EntryStateChange, ExtractedSource, SaveBatch
from transbridge.persistence.project_store import alignment_cache_schema
from transbridge.persistence.project_store.alignment_cache import ProjectAlignmentCache
from transbridge.persistence.project_store.archive import ProjectPackage
from transbridge.persistence.project_store.catalog import ActiveProjectPreference, ProjectCatalog
from transbridge.persistence.project_store.connection import project_connection
from transbridge.persistence.project_store.entry_codec import STATE_FIELDS
from transbridge.persistence.project_store.format import (
    PROJECT_FILENAME,
    PROJECT_FORMAT,
    PROJECT_FORMAT_VERSION,
    UnsupportedProjectFormat,
)
from transbridge.persistence.project_store.records import WorkspaceProjects
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.persistence.project_store.writes import insert_project, write_state
from transbridge.persistence.v2 import OsPersistenceFilesystem
from transbridge.persistence.v2.ids import ProjectId, ProjectRef, VariantId, VariantRef


@pytest.fixture
def cached_project(tmp_path):
    namespace = SourceNamespace("source:alignment")
    entries = tuple(_stored_entry(EntryKey(namespace, str(i)), "Nord", "诺德和诺德") for i in range(3))
    source = ExtractedSource(namespace, "Source.json", FormatId.JSON_DSD, "a" * 64, entries)
    path = tmp_path / "project.sqlite3"
    with project_connection(path, write=True, create=True) as connection:
        insert_project(connection, ProjectId("alignment"), "Alignment", VariantId("a"), "A", (source,))
        connection.execute("INSERT INTO variant (identity, name, ordinal, revision) VALUES ('b', 'B', 1, 0)")
    store, cache = ProjectStore(path), ProjectAlignmentCache(path)
    inputs = tuple(
        (row.snapshot.entry_key.serialize(), row.snapshot.original, row.state.translation) for row in entries
    )
    return store, cache, entries, inputs


def _write(store, variant, key, **fields):
    token = store.read_token(VariantId(variant))
    store.commit(
        SaveBatch(
            token.project_id,
            token.variant_id,
            token.project_revision,
            token.variant_revision,
            (EntryStateChange(key, tuple(fields.items())),),
        )
    )


def _seed(cache, variant, inputs):
    snapshot = cache.capture(variant, inputs)
    assert cache.publish(snapshot, ((key, "request", "decision") for key, *_ in inputs)) == len(inputs)
    return snapshot


def test_cache_reopens_but_never_crosses_variant(cached_project):
    store, cache, entries, inputs = cached_project
    _seed(cache, "a", inputs)
    reopened = ProjectAlignmentCache(store.path)
    assert reopened.capture("a", inputs).records[inputs[0][0]] == (("request", "decision"),)
    assert not any(reopened.capture("b", inputs).records.values())
    with project_connection(store.path, write=True) as connection:
        connection.execute("INSERT INTO variant (identity, name, ordinal, revision) VALUES ('copy', 'Copy', 2, 0)")
        connection.execute(
            f"INSERT INTO entry_state (variant_id, entry_id, {STATE_FIELDS}) "
            f"SELECT 'copy', entry_id, {STATE_FIELDS} FROM entry_state WHERE variant_id = 'a'"
        )
    assert not any(cache.capture("copy", inputs).records.values())


def test_warm_capture_does_not_issue_writes_or_allocate_new_nonces(cached_project, monkeypatch):
    from transbridge.persistence.project_store import alignment_cache

    store, cache, _entries, inputs = cached_project
    _seed(cache, "a", inputs)
    before = store.path.read_bytes()

    def unexpected_nonce():
        raise AssertionError("warm cache must reuse its existing invalidation fence")

    monkeypatch.setattr(alignment_cache, "uuid4", unexpected_nonce)
    snapshot = cache.capture("a", inputs)
    assert all(snapshot.records.values())
    assert store.path.read_bytes() == before


def test_translation_edit_clears_only_changed_entry_and_rejects_aba_publication(cached_project):
    store, cache, entries, inputs = cached_project
    before = _seed(cache, "a", inputs)
    _seed(cache, "b", inputs)
    key = entries[0].snapshot.entry_key
    _write(store, "a", key, translation="Edited")
    _write(store, "a", key, translation=inputs[0][2])
    assert cache.publish(before, ((inputs[0][0], "late", "old decision"),)) == 0
    current = cache.capture("a", inputs)
    assert current.records[inputs[0][0]] == ()
    assert current.records[inputs[1][0]]
    assert all(cache.capture("b", inputs).records.values())


def test_valid_entries_rechecks_aba_and_variant_deletion_without_writes(cached_project):
    store, cache, entries, inputs = cached_project
    before = _seed(cache, "a", inputs)
    b = _seed(cache, "b", inputs)
    assert cache.valid_entries(before) == frozenset(row[0] for row in inputs)
    key = entries[0].snapshot.entry_key
    _write(store, "a", key, translation="Changed")
    _write(store, "a", key, translation=inputs[0][2])
    bytes_before = store.path.read_bytes()
    assert cache.valid_entries(before) == frozenset(row[0] for row in inputs[1:])
    assert store.path.read_bytes() == bytes_before
    with project_connection(store.path, write=True) as connection:
        connection.execute("DELETE FROM variant WHERE identity = 'b'")
    assert cache.valid_entries(b) == frozenset()


def test_valid_entries_rejects_snapshot_from_another_project(cached_project):
    _store, cache, _entries, inputs = cached_project
    snapshot = cache.capture("a", inputs)
    with pytest.raises(RuntimeError, match="another project"):
        cache.valid_entries(replace(snapshot, project_id="another-project"))


@pytest.mark.parametrize("fields", [{"translation": "诺德和诺德"}, {"stage": Stage.TRANSLATED}, {"labels": ("tag",)}])
def test_noop_translation_stage_and_labels_preserve_cache(cached_project, fields):
    store, cache, entries, inputs = cached_project
    before = _seed(cache, "a", inputs)
    _write(store, "a", entries[0].snapshot.entry_key, **fields)
    current = cache.capture("a", inputs)
    assert current.tokens[inputs[0][0]] == before.tokens[inputs[0][0]]
    assert current.records[inputs[0][0]]


def test_sparse_state_removal_compares_effective_translation(cached_project):
    store, cache, entries, inputs = cached_project
    _write(store, "a", entries[0].snapshot.entry_key, labels=("tag",))
    _seed(cache, "a", inputs)
    with project_connection(store.path, write=True) as connection:
        connection.execute("DELETE FROM entry_state WHERE variant_id = 'a'")
    assert cache.capture("a", inputs).records[inputs[0][0]]
    _write(store, "a", entries[0].snapshot.entry_key, translation="Changed")
    changed = ((inputs[0][0], inputs[0][1], "Changed"),)
    _seed(cache, "a", changed)
    with project_connection(store.path, write=True) as connection:
        connection.execute("DELETE FROM entry_state WHERE variant_id = 'a'")
    assert cache.capture("a", inputs).records[inputs[0][0]] == ()


def test_shared_original_change_invalidates_all_variants_even_after_undo(cached_project):
    store, cache, entries, inputs = cached_project
    a = _seed(cache, "a", inputs)
    _seed(cache, "b", inputs)
    with project_connection(store.path, write=True) as connection:
        connection.execute("UPDATE source_entry SET original = 'Changed' WHERE local_key = '0'")
        connection.execute("UPDATE source_entry SET original = 'Nord' WHERE local_key = '0'")
    assert cache.publish(a, ((inputs[0][0], "late", "old"),)) == 0
    for variant in ("a", "b"):
        assert cache.capture(variant, inputs).records[inputs[0][0]] == ()
        assert cache.capture(variant, inputs).records[inputs[1][0]]


def test_changed_defaults_invalidate_only_variants_using_default(cached_project):
    store, cache, entries, inputs = cached_project
    with project_connection(store.path, write=True) as connection:
        write_state(connection, VariantId("b"), 1, replace(entries[0].state, translation="Override"))
    _seed(cache, "a", inputs)
    b_input = ((inputs[0][0], inputs[0][1], "Override"),)
    _seed(cache, "b", b_input)
    with project_connection(store.path, write=True) as connection:
        connection.execute("UPDATE entry_default SET translation = 'Default changed' WHERE entry_id = 1")
    changed = ((inputs[0][0], inputs[0][1], "Default changed"),)
    assert cache.capture("a", changed).records[inputs[0][0]] == ()
    assert cache.capture("b", b_input).records[inputs[0][0]]


@pytest.mark.parametrize("all_entries", [False, True])
def test_clear_invalidates_unpublished_tokens_and_preserves_other_variant(cached_project, all_entries):
    _store, cache, _entries, inputs = cached_project
    before = cache.capture("a", inputs)
    _seed(cache, "b", inputs)
    cache.clear("a", None if all_entries else (inputs[0][0],))
    assert cache.publish(before, ((inputs[0][0], "late", "result"),)) == 0
    assert all(cache.capture("b", inputs).records.values())


def test_dirty_input_cannot_load_or_publish_persisted_records(cached_project):
    _store, cache, _entries, inputs = cached_project
    _seed(cache, "a", inputs)
    dirty = cache.capture("a", ((inputs[0][0], "Nord", "Unsaved text"),))
    assert not dirty.tokens and not dirty.records
    assert cache.publish(dirty, ((inputs[0][0], "request", "result"),)) == 0


def test_source_unpublish_and_variant_delete_remove_cache(cached_project):
    store, cache, _entries, inputs = cached_project
    before = _seed(cache, "a", inputs)
    _seed(cache, "b", inputs)
    with project_connection(store.path, write=True) as connection:
        connection.execute("DELETE FROM variant WHERE identity = 'b'")
        assert (
            connection.execute("SELECT COUNT(*) FROM alignment_cache_fence WHERE variant_id = 'b'").fetchone()[0] == 0
        )
        connection.execute("DELETE FROM project_source")
    assert cache.publish(before, ((inputs[0][0], "late", "result"),)) == 0


def test_capacity_is_bounded_per_entry_and_variant(cached_project):
    store, _cache, _entries, inputs = cached_project
    cache = ProjectAlignmentCache(store.path, max_per_entry=2, max_per_variant=3)
    snapshot = cache.capture("a", inputs)
    for key, *_ in inputs:
        cache.publish(snapshot, ((key, str(i), "result") for i in range(4)))
    result = cache.capture("a", inputs)
    assert all(len(records) <= 2 for records in result.records.values())
    assert sum(map(len, result.records.values())) == 3


def test_entry_eviction_uses_entry_prefix_instead_of_scanning_variant(cached_project):
    store, cache, _entries, inputs = cached_project
    _seed(cache, "a", inputs)
    with project_connection(store.path) as connection:
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM alignment_cache_record WHERE variant_id = ? AND entry_id = ? "
            "ORDER BY used DESC, id DESC LIMIT -1 OFFSET ?",
            ("a", 1, 16),
        ).fetchall()
    details = " ".join(row["detail"] for row in plan)
    assert "alignment_cache_entry_eviction" in details
    assert "variant_id=? AND entry_id=?" in details
    assert "TEMP B-TREE" not in details


def test_cache_extension_is_optional_and_only_exact_complete_schema_is_admitted(cached_project):
    store, cache, _entries, inputs = cached_project
    assert store.summary()
    _seed(cache, "a", inputs)
    assert store.summary()
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER alignment_state_update")
    with pytest.raises(UnsupportedProjectFormat, match="schema differs"):
        store.summary()


def test_strip_portable_copy_restores_base_schema(cached_project):
    store, cache, _entries, inputs = cached_project
    _seed(cache, "a", inputs)
    with project_connection(store.path, write=True) as connection:
        alignment_cache_schema.remove(connection)
    assert store.summary()
    assert not any(cache.capture("a", inputs).records.values())


def test_failed_translation_transaction_preserves_cache(cached_project):
    store, cache, _entries, inputs = cached_project
    before = _seed(cache, "a", inputs)
    with pytest.raises(RuntimeError, match="rollback"):
        with project_connection(store.path, write=True) as connection:
            connection.execute("UPDATE entry_default SET translation = 'Changed' WHERE entry_id = 1")
            raise RuntimeError("rollback")
    after = cache.capture("a", inputs)
    assert after.tokens == before.tokens
    assert all(after.records.values())


def test_oversized_record_is_not_persisted(cached_project):
    store, _cache, _entries, inputs = cached_project
    cache = ProjectAlignmentCache(store.path, max_record_bytes=8)
    snapshot = cache.capture("a", inputs)
    assert cache.publish(snapshot, ((inputs[0][0], "request", "too much payload"),)) == 0
    assert not any(cache.capture("a", inputs).records.values())


@pytest.mark.parametrize("raw_package", [False, True])
def test_project_package_excludes_cache_and_import_discards_embedded_cache(tmp_path, raw_package):
    plugin = write_plugin(tmp_path / "Source.esp", [(0x800, "Nord", "Nord")])
    root = tmp_path / "workspace"
    path, variant = _create_project(root, plugin, FormatId.PLUGIN_SSE, role="primary")
    store = ProjectStore(path)
    summary = store.summary()
    entry = store.read_page(variant)[0]
    inputs = ((entry.snapshot.entry_key.serialize(), entry.snapshot.original, entry.state.translation),)
    cache = ProjectAlignmentCache(path)
    _seed(cache, variant.value, inputs)
    package = tmp_path / "cache-test.transbridge"
    projects = WorkspaceProjects(root)
    catalog = ProjectCatalog(projects, ActiveProjectPreference(projects.root, OsPersistenceFilesystem()))
    context = RequestContext("cache-package")
    lifecycle = SimpleNamespace(
        active=SimpleNamespace(
            variant=object(),
            dirty=False,
            project_ref=ProjectRef(summary.identity),
            formal_variant_ref=VariantRef(variant, summary.identity),
            persisted_project_revision=summary.revision,
            persisted_variant_revision=summary.variants[0].revision,
        ),
        acquire_export_lease=lambda _context: SimpleNamespace(is_success=True, value={"token": "cache-export"}),
        validate_export_lease=lambda *_args: SimpleNamespace(is_success=True),
    )
    if raw_package:
        with ZipFile(package, "w") as archive:
            archive.writestr(
                "manifest.json",
                json.dumps({
                    "format": PROJECT_FORMAT,
                    "version": PROJECT_FORMAT_VERSION,
                    "project_id": summary.identity.value,
                    "members": [PROJECT_FILENAME],
                }),
            )
            archive.write(path, PROJECT_FILENAME)
    else:
        ProjectPackage(projects, catalog, lifecycle).export_project(str(package), context)
        with ZipFile(package) as archive:
            exported = tmp_path / "exported.sqlite3"
            exported.write_bytes(archive.read(PROJECT_FILENAME))
        with project_connection(exported) as connection:
            assert not alignment_cache_schema.installed(connection)
    destination = WorkspaceProjects(tmp_path / "destination")
    destination_catalog = ProjectCatalog(
        destination, ActiveProjectPreference(destination.root, OsPersistenceFilesystem())
    )
    imported = ProjectPackage(destination, destination_catalog, None).import_project(str(package), context)
    with project_connection(imported) as connection:
        assert not alignment_cache_schema.installed(connection)
    assert cache.capture(variant.value, inputs).records[inputs[0][0]]
