from dataclasses import replace
import sqlite3

import pytest

from transbridge.application.io import FormatId, Stage
from transbridge.application.io.identity import EntryKey, EntryRevision, ExternalEntryRef, Provenance, SourceNamespace
from transbridge.application.io.mutation import EntrySnapshot
from transbridge.application.projects.storage_contracts import EntryStateChange, ExtractedSource, SaveBatch, StoredEntry
from transbridge.application.projects.working_copy import ProjectWorkingCopy
from transbridge.persistence.project_store.format import ProjectStorageConflict, ProjectStoreError
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.persistence.v2.ids import ProjectId, VariantId
from transbridge.persistence.v2.variant import VariantEntryState


def _stored_entry(key, original, translation, *, context=None, string_id=None):
    state = VariantEntryState(key, translation, stage=Stage.REVIEWED)
    snapshot = EntrySnapshot(
        entry_key=key,
        legacy_id="same-legacy-id",
        original=original,
        translation=translation,
        stage=state.stage.value,
        context=context,
        external_refs=(),
        revision=state.revision,
        provenance=(),
        metadata=(),
        string_id=string_id,
    )
    return StoredEntry(snapshot, state)


def test_reopen_distinguishes_complete_entry_keys_and_preserves_source_order(tmp_path):
    first = SourceNamespace("source:first")
    second = SourceNamespace("source:second")
    without_original = _stored_entry(EntryKey(first, "shared"), "原文", "not original-matched", context="")
    empty_original = _stored_entry(EntryKey(first, "shared", ""), "", "", context=None, string_id=0)
    matching_original = _stored_entry(EntryKey(first, "shared", "原文"), "原文", "匹配译文", context="NPC_:FULL")
    other_source = _stored_entry(EntryKey(second, "shared", "原文"), "原文", "另一来源")
    state = replace(
        empty_original.state,
        labels=("reviewed", "term"),
        external_refs=(ExternalEntryRef("paratranz", "project-42", 0),),
        provenance=(Provenance("run-1", "reviewer", "manual"),),
        revision=EntryRevision(8),
    )
    empty_original = StoredEntry(
        replace(
            empty_original.snapshot,
            external_refs=state.external_refs,
            provenance=state.provenance,
            revision=state.revision,
        ),
        state,
    )
    expected = (without_original, empty_original, matching_original, other_source)
    sources = (
        ExtractedSource(first, "Same filename.json", FormatId.JSON_DSD, "a" * 64, expected[:3]),
        ExtractedSource(second, "Same filename.json", FormatId.JSON_DSD, "b" * 64, expected[3:]),
    )
    path = tmp_path / "project.sqlite3"
    project_id, variant_id = ProjectId("entry-identity"), VariantId("main")
    ProjectStore(path).create(project_id, "Entry identity", variant_id, "Main", sources=sources)

    reopened = ProjectStore(path)
    assert reopened.summary().identity == project_id
    assert tuple(source.namespace for source in reopened.sources()) == (first, second)
    assert reopened.read_page(variant_id, limit=2) == expected[:2]
    assert reopened.read_page(variant_id, offset=2, limit=2) == expected[2:]
    assert reopened.read_page(variant_id, namespace=second) == (other_source,)
    requested = (
        other_source.snapshot.entry_key,
        empty_original.snapshot.entry_key,
        without_original.snapshot.entry_key,
    )
    by_key = {entry.snapshot.entry_key: entry for entry in reopened.read_entries(variant_id, requested)}
    assert by_key == {entry.snapshot.entry_key: entry for entry in (other_source, empty_original, without_original)}


def test_duplicate_identity_in_second_source_does_not_publish_a_partial_project(tmp_path):
    first, second = SourceNamespace("source:first"), SourceNamespace("source:second")
    valid = _stored_entry(EntryKey(first, "entry"), "Hello", "")
    duplicate = _stored_entry(EntryKey(second, "entry"), "Another source", "")
    sources = (
        ExtractedSource(first, "First.json", FormatId.JSON_DSD, "a" * 64, (valid,)),
        ExtractedSource(second, "Second.json", FormatId.JSON_DSD, "b" * 64, (duplicate, duplicate)),
    )
    path = tmp_path / "project.sqlite3"
    with pytest.raises(ProjectStoreError, match="UNIQUE"):
        ProjectStore(path).create(ProjectId("duplicate"), "Duplicate", VariantId("main"), "Main", sources=sources)
    assert not tuple(tmp_path.iterdir())


@pytest.mark.parametrize(
    "metadata",
    [
        (("io.locator", ("reference", ("file:///private/source.esp",))),),
        (("io.locator", ("reference", (b"binary plugin material",))),),
    ],
    ids=["nested-historical-path", "nested-bytes"],
)
def test_store_does_not_allow_nested_plugin_metadata_to_bypass_extraction(tmp_path, metadata):
    namespace = SourceNamespace("source:untrusted")
    original = _stored_entry(EntryKey(namespace, "entry"), "Hello", "")
    tampered = replace(original, snapshot=replace(original.snapshot, metadata=metadata))
    source = ExtractedSource(namespace, "Source.esp", FormatId.PLUGIN_SSE, "a" * 64, (tampered,))

    with pytest.raises(ValueError, match="persistent source metadata"):
        ProjectStore(tmp_path / "project.sqlite3").create(
            ProjectId("untrusted-source"), "Untrusted source", VariantId("main"), "Main", sources=(source,)
        )

    assert not tuple(tmp_path.iterdir())


@pytest.fixture
def project(tmp_path):
    project_id, variant = ProjectId("sparse-save"), VariantId("main")
    path = tmp_path / "project.sqlite3"
    sources = []
    for index, count in enumerate((260, 5)):
        namespace = SourceNamespace(f"source:page-{index}")
        entries = tuple(
            _stored_entry(EntryKey(namespace, f"key-{number}"), f"Original {number}", f"Translation {number}")
            for number in range(count)
        )
        sources.append(ExtractedSource(namespace, f"Source-{index}.json", FormatId.JSON_DSD, "b" * 64, entries))
    ProjectStore(path).create(project_id, "Sparse save", variant, "Main", sources=tuple(sources))
    return path, project_id, variant, sources[0].entries + sources[1].entries


def test_single_row_save_preserves_unloaded_entries_and_performs_bounded_writes(project, monkeypatch):
    path, project_id, variant, original_entries = project
    store = ProjectStore(path)
    (loaded,) = store.read_page(variant, limit=1)
    key = loaded.snapshot.entry_key
    writes = []
    connect = sqlite3.connect

    class MeasuringConnection(sqlite3.Connection):
        def close(self):
            writes.append(self.total_changes)
            super().close()

    def measuring_connect(*args, **kwargs):
        return connect(*args, **kwargs, factory=MeasuringConnection)

    batch = SaveBatch(
        project_id,
        variant,
        0,
        0,
        (EntryStateChange(key, (("translation", ""),), loaded.state.revision),),
    )
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", measuring_connect)
        receipt = store.commit(batch)

    # A single state row plus project and variant revisions. SQLite counts
    # individual changed rows, including any redundant delete/reinsert.
    assert 1 <= sum(writes) <= 3
    assert receipt.project_revision == receipt.variant_revision == 1
    assert [(entry.entry_key, entry.translation, entry.stage) for entry in receipt.entries] == [
        (key, "", Stage.REVIEWED)
    ]
    reopened = ProjectStore(path)
    (saved,) = reopened.read_entries(variant, (key,))
    assert saved.snapshot.translation == ""
    assert saved.state.revision == loaded.state.revision.next()
    assert reopened.read_page(variant, offset=1, limit=len(original_entries)) == original_entries[1:]
    all_keys = [entry.snapshot.entry_key for entry in original_entries]
    assert tuple(entry.snapshot.entry_key for entry in reopened.read_entries(variant, all_keys)) == tuple(all_keys)


def test_stale_writer_is_rejected_and_repeated_operation_preserves_later_edit(project):
    path, project_id, variant, entries = project
    first, other = ProjectStore(path), ProjectStore(path)
    key = entries[0].state.entry_key
    initial = SaveBatch(
        project_id,
        variant,
        0,
        0,
        (EntryStateChange(key, (("translation", "Applied result"),), EntryRevision()),),
        operation_id="apply-result",
    )
    stale = replace(initial, changes=(EntryStateChange(key, (("translation", "Stale write"),)),), operation_id=None)

    receipt = first.commit(initial)
    committed_bytes = path.read_bytes()
    with pytest.raises(ProjectStorageConflict, match="revision"):
        other.commit(stale)
    assert path.read_bytes() == committed_bytes

    subsequent = replace(
        stale,
        expected_project_revision=receipt.project_revision,
        expected_variant_revision=receipt.variant_revision,
        changes=(EntryStateChange(key, (("translation", "Manual update"),), EntryRevision(1)),),
    )
    other.commit(subsequent)
    after_manual_edit = path.read_bytes()
    assert first.commit(initial) == receipt
    with pytest.raises(ProjectStorageConflict, match="operation ID"):
        first.commit(replace(subsequent, operation_id=initial.operation_id))
    assert path.read_bytes() == after_manual_edit
    assert ProjectStore(path).read_entries(variant, (key,))[0].state.translation == "Manual update"


def test_revision_bound_pages_refuse_to_mix_rows_from_another_commit(project):
    path, _project_id, variant, entries = project
    reader = ProjectStore(path)
    token = reader.read_token(variant)
    assert reader.read_page(variant, limit=2, token=token) == entries[:2]
    key = entries[2].state.entry_key
    writer = ProjectWorkingCopy(ProjectStore(path), variant)
    writer.edit(key, translation="New page value")
    writer.save()

    with pytest.raises(ProjectStorageConflict, match="changed"):
        reader.read_page(variant, offset=2, limit=2, token=token)
    with pytest.raises(ProjectStorageConflict, match="changed"):
        reader.read_entries(variant, (key,), token=token)

    current = reader.read_token(variant)
    assert current != token
    changed, untouched = reader.read_page(variant, offset=2, limit=2, token=current)
    assert changed.state.translation == "New page value"
    assert untouched == entries[3]


def test_failed_multi_row_save_rolls_back_earlier_writes_and_receipt(project):
    path, project_id, variant, entries = project
    store = ProjectStore(path)
    first, second = (entry.state.entry_key for entry in entries[:2])
    proposed = SaveBatch(
        project_id,
        variant,
        0,
        0,
        (
            EntryStateChange(first, (("translation", "New first value"),), EntryRevision()),
            EntryStateChange(second, (("translation", "New second value"),), EntryRevision(3)),
        ),
        operation_id="mixed-revisions",
    )
    before = path.read_bytes()

    with pytest.raises(ProjectStorageConflict, match="entry revision"):
        store.commit(proposed)

    assert path.read_bytes() == before
    reopened = ProjectStore(path)
    assert reopened.summary().revision == 0
    assert reopened.read_entries(variant, (first, second)) == entries[:2]
    corrected = replace(proposed, changes=(proposed.changes[0], replace(proposed.changes[1], expected_revision=None)))
    assert reopened.commit(corrected).project_revision == 1


@pytest.mark.parametrize("later_translation", ["Edited during save", "Translation 0"])
def test_save_acknowledgement_retains_edits_made_after_capture(project, later_translation):
    path, _project_id, variant, entries = project
    store = ProjectStore(path)
    working = ProjectWorkingCopy(store, variant)
    first, second = (entry.state.entry_key for entry in entries[:2])
    working.edit(first, translation="Captured")
    captured = working.capture()
    working.edit(first, translation=later_translation)
    working.edit(second, labels=("new-label",))

    working.acknowledge(store.commit(captured))

    assert working.dirty
    saved_first, saved_second = ProjectStore(path).read_entries(variant, (first, second))
    assert saved_first.state.translation == "Captured"
    assert saved_second.state.labels == ()
    receipt = working.save()
    assert receipt.project_revision == 2
    assert not working.dirty
    saved_first, saved_second = ProjectStore(path).read_entries(variant, (first, second))
    assert saved_first.state.translation == later_translation
    assert saved_second.state.labels == ("new-label",)


def test_clean_working_copy_save_does_not_acquire_write_lock(project):
    path, _project_id, variant, entries = project
    working = ProjectWorkingCopy(ProjectStore(path), variant)
    first = entries[0].state
    working.edit(first.entry_key, translation=first.translation)
    assert not working.dirty
    before = path.read_bytes()

    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        receipt = working.save()
    finally:
        connection.close()

    assert not receipt.entries
    assert receipt.project_revision == receipt.variant_revision == 0
    assert path.read_bytes() == before


def test_save_with_operation_id_persists_stage_and_can_return_the_same_receipt(project):
    path, _project_id, variant, entries = project
    store = ProjectStore(path)
    working = ProjectWorkingCopy(store, variant)
    key = entries[0].state.entry_key
    working.edit(key, stage=Stage.LOCKED)
    batch = working.capture("stage-save")

    receipt = store.commit(batch)
    working.acknowledge(receipt)

    assert not working.dirty
    assert ProjectStore(path).read_entries(variant, (key,))[0].state.stage is Stage.LOCKED
    assert store.commit(batch) == receipt


def test_late_receipt_from_an_earlier_save_cannot_acknowledge_the_current_batch(project):
    path, _project_id, variant, entries = project
    store = ProjectStore(path)
    working = ProjectWorkingCopy(store, variant)
    key = entries[0].state.entry_key
    working.edit(key, translation="First edit")
    earlier = working.save()
    working.edit(key, translation="Second edit")
    current = working.capture()

    with pytest.raises(ValueError, match="receipt"):
        working.acknowledge(earlier)

    assert working.dirty
    working.acknowledge(store.commit(current))
    assert not working.dirty
    assert ProjectStore(path).read_entries(variant, (key,))[0].state.translation == "Second edit"
