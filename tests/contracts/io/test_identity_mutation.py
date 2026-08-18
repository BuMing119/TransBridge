from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib

import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.io import (
    ChangeSet,
    EntryKey,
    EntryPatch,
    EntryRevision,
    ExternalEntryRef,
    MutationStatus,
    Provenance,
    SourceNamespace,
)
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.parser.xt import XT_Entry


def _namespace(label: str) -> SourceNamespace:
    digest = hashlib.sha256(label.encode()).hexdigest()
    return SourceNamespace.from_fingerprint("plugin.sse", digest, scope="project-1")


def _entry(
    namespace: SourceNamespace,
    local_key: str,
    *,
    external_refs: tuple[ExternalEntryRef, ...] = (),
    revision: int = 0,
) -> TranslationEntry:
    return TranslationEntry(
        id=f"legacy:{local_key}",
        key=local_key,
        original="Hello",
        translation="",
        stage=0,
        context="INFO:NAM1",
        entry_key=EntryKey(namespace, local_key),
        external_refs=external_refs,
        revision=EntryRevision(revision),
        metadata=(("plugin.record_type", "INFO:NAM1"),),
    )


def _changeset(
    run_id: str,
    actor: str,
    patches: tuple[EntryPatch, ...],
    expected: tuple[tuple[EntryKey, EntryRevision], ...],
) -> ChangeSet:
    return ChangeSet(run_id, patches, expected, Provenance(run_id, actor, "contract-test"))


def _context(run_id: str, *permissions: str) -> RequestContext:
    return RequestContext("actor-1", run_id=run_id, permissions=frozenset(permissions))


@pytest.mark.parametrize(
    "scope",
    [
        "C:\\Users\\customer\\project.esp",
        "C:/project.esp",
        "C:",
        "profile:C:",
        "/home/customer/project.esp",
        "file:///project.esp",
    ],
)
def test_source_namespace_fingerprint_rejects_absolute_path_scope(scope: str) -> None:
    digest = hashlib.sha256(b"source").hexdigest()

    with pytest.raises(ValueError, match="stable non-path token|absolute path"):
        SourceNamespace.from_fingerprint("plugin.sse", digest, scope=scope)


def test_source_namespace_direct_value_cannot_hide_absolute_path() -> None:
    with pytest.raises(ValueError, match="absolute path"):
        SourceNamespace("source:plugin.sse:C:\\private\\file.esp:sha256:" + "a" * 64)

    with pytest.raises(ValueError, match="absolute path"):
        SourceNamespace("source:plugin.sse:C::sha256:" + "a" * 64)


@pytest.mark.parametrize("value", [True, 1.5, "1", -1])
def test_entry_revision_accepts_only_non_negative_int(value) -> None:
    with pytest.raises(ValueError, match="non-negative integer"):
        EntryRevision(value)


@pytest.mark.parametrize("value", [True, "1"])
def test_translation_entry_rejects_revision_coercion(value) -> None:
    with pytest.raises(ValueError, match="non-negative integer"):
        TranslationEntry("id", "key", "original", "", 0, None, revision=value)


@pytest.mark.parametrize("value", [True, "1"])
def test_translation_entry_from_dict_rejects_revision_coercion(value) -> None:
    with pytest.raises(ValueError, match="non-negative integer"):
        TranslationEntry.from_dict({
            "id": "id",
            "key": "key",
            "original": "original",
            "translation": "",
            "stage": 0,
            "revision": value,
        })


def test_translation_entry_keeps_legacy_integer_revision_compatible() -> None:
    direct = TranslationEntry("id", "key", "original", "", 0, None, revision=3)
    restored = TranslationEntry.from_dict(direct.to_dict())

    assert direct.revision == EntryRevision(3)
    assert restored.revision == EntryRevision(3)


def test_same_local_key_in_two_namespaces_never_overwrites() -> None:
    first = _entry(_namespace("one"), "shared")
    second = _entry(_namespace("two"), "shared")
    collection = TranslationEntryCollection((first, second))

    assert len(collection) == 2
    assert collection.get(first.identity) is first
    assert collection.get(second.identity) is second
    with pytest.warns(RuntimeWarning, match="ambiguous"):
        assert collection.get("shared") is None
    assert collection.legacy_mapping_report().ambiguous_local_keys == ("shared",)
    assert not hasattr(collection, "_id_index")


def test_external_id_change_does_not_change_entry_key() -> None:
    old_ref = ExternalEntryRef("paratranz", "project:7/file:3", 100)
    new_ref = ExternalEntryRef("paratranz", "project:7/file:3", 900)
    entry = _entry(_namespace("entry"), "stable-key", external_refs=(old_ref,))
    collection = TranslationEntryCollection((entry,))
    change_set = _changeset(
        "run-ref",
        "actor-1",
        (EntryPatch.create(entry.identity, external_refs=(new_ref,)),),
        ((entry.identity, entry.revision),),
    )

    result = collection.apply(change_set, _context("run-ref", "entry.external_refs.write"))
    updated = collection.get(entry.identity)

    assert result.status is MutationStatus.APPLIED
    assert updated is not None and updated.identity == entry.identity
    assert updated.external_refs == (new_ref,)
    assert updated.revision == EntryRevision(1)
    assert updated.provenance[-1].run_id == "run-ref"
    assert collection.get_by_external_ref(old_ref) == ()
    assert collection.get_by_external_ref(new_ref) == (updated,)


def test_duplicate_external_ref_rejects_entire_changeset_atomically() -> None:
    first = _entry(_namespace("one"), "one")
    second = _entry(_namespace("two"), "two")
    duplicate = ExternalEntryRef("paratranz", "project:7/file:3", 77)
    collection = TranslationEntryCollection((first, second))
    before = (collection.snapshot(first.identity), collection.snapshot(second.identity))
    change_set = _changeset(
        "run-duplicate",
        "actor-1",
        (
            EntryPatch.create(first.identity, translation="translated", external_refs=(duplicate,)),
            EntryPatch.create(second.identity, external_refs=(duplicate,)),
        ),
        ((first.identity, first.revision), (second.identity, second.revision)),
    )

    result = collection.apply(
        change_set,
        _context("run-duplicate", "entry.translation.write", "entry.external_refs.write"),
    )

    assert result.status is MutationStatus.CONFLICT
    assert result.diagnostics[0].code == "EXTERNAL_REF_CONFLICT"
    assert (collection.snapshot(first.identity), collection.snapshot(second.identity)) == before


def test_revision_race_rejects_all_patches_without_partial_commit() -> None:
    first = _entry(_namespace("one"), "one")
    second = _entry(_namespace("two"), "two", revision=2)
    collection = TranslationEntryCollection((first, second))
    change_set = _changeset(
        "run-race",
        "actor-1",
        (
            EntryPatch.create(first.identity, translation="first"),
            EntryPatch.create(second.identity, translation="second"),
        ),
        ((first.identity, EntryRevision(0)), (second.identity, EntryRevision(1))),
    )

    result = collection.apply(change_set, _context("run-race", "entry.translation.write"))

    assert result.status is MutationStatus.CONFLICT
    assert result.diagnostics[0].code == "ENTRY_REVISION_CONFLICT"
    assert collection.get(first.identity).translation == ""
    assert collection.get(second.identity).translation == ""


def test_permissions_come_only_from_trusted_request_context() -> None:
    entry = _entry(_namespace("permissions"), "one")
    collection = TranslationEntryCollection((entry,))
    change_set = _changeset(
        "run-permission",
        "actor-1",
        (EntryPatch.create(entry.identity, stage=1),),
        ((entry.identity, entry.revision),),
    )

    denied = collection.apply(change_set, _context("run-permission"))
    applied = collection.apply(change_set, _context("run-permission", "entry.stage.write"))

    assert denied.status is MutationStatus.REJECTED
    assert denied.diagnostics[0].code == "ENTRY_FIELD_PERMISSION_DENIED"
    assert applied.status is MutationStatus.APPLIED
    assert collection.get(entry.identity).stage == 1


def test_run_and_actor_must_match_trusted_context() -> None:
    entry = _entry(_namespace("run"), "one")
    collection = TranslationEntryCollection((entry,))
    change_set = _changeset(
        "run-expected",
        "actor-1",
        (EntryPatch.create(entry.identity, translation="x"),),
        ((entry.identity, entry.revision),),
    )

    wrong_run = collection.apply(
        change_set,
        RequestContext("actor-1", run_id="run-other", permissions=frozenset({"entry.translation.write"})),
    )
    wrong_actor = collection.apply(
        change_set,
        RequestContext("actor-2", run_id="run-expected", permissions=frozenset({"entry.translation.write"})),
    )

    assert wrong_run.status is MutationStatus.REJECTED
    assert wrong_run.diagnostics[0].code == "CHANGESET_RUN_CONTEXT_MISMATCH"
    assert wrong_actor.status is MutationStatus.REJECTED
    assert wrong_actor.diagnostics[0].code == "CHANGESET_ACTOR_CONTEXT_MISMATCH"


def test_v2_entry_serialization_round_trip_preserves_identity_envelope() -> None:
    reference = ExternalEntryRef("paratranz", "project:7/file:3", 42, (("source.field", "id"),))
    provenance = Provenance("run-1", "actor-1", "fixture", "2026-08-18T00:00:00Z")
    entry = _entry(_namespace("roundtrip"), "stable", external_refs=(reference,), revision=4)
    entry = TranslationEntry.from_dict({
        **entry.to_dict(),
        "provenance": [provenance.to_dict()],
        "metadata": {"plugin.record_type": "INFO:NAM1"},
    })

    restored = TranslationEntry.from_dict(entry.to_dict())

    assert restored == entry
    assert restored.id != restored.identity.local_key
    assert restored.external_refs[0].opaque_id == 42


def test_external_ref_json_scalar_types_survive_entry_serialization() -> None:
    scalar_values = (True, 1, 1.0, None, "1")
    references = tuple(ExternalEntryRef("paratranz", "scalar-types", value) for value in scalar_values)
    entry = _entry(_namespace("scalar-types"), "stable", external_refs=references)

    restored = TranslationEntry.from_dict(entry.to_dict())

    assert [(type(ref.opaque_id), ref.opaque_id) for ref in restored.external_refs] == [
        (type(value), value) for value in scalar_values
    ]
    assert len({ref.index_key for ref in restored.external_refs}) == len(scalar_values)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_external_ref_rejects_non_finite_json_numbers(value: float) -> None:
    with pytest.raises(ValueError, match="finite JSON number"):
        ExternalEntryRef("paratranz", "scalar-types", value)


def test_v1_load_maps_to_read_only_legacy_namespace() -> None:
    entry = TranslationEntry.from_dict({
        "id": "legacy-id",
        "key": "legacy-key",
        "original": "a",
        "translation": "b",
        "stage": 1,
    })
    collection = TranslationEntryCollection((entry,))
    report = collection.legacy_mapping_report()

    assert entry.identity == EntryKey(SourceNamespace.legacy(), "legacy-key")
    assert report.mappings[0].entry_key == entry.identity
    with pytest.raises(FrozenInstanceError):
        report.mappings[0].legacy_key = "changed"  # type: ignore[misc]


def test_legacy_add_facade_preserves_v2_envelope_without_second_index() -> None:
    reference = ExternalEntryRef("paratranz", "project:7/file:3", 42)
    provenance = Provenance("run-old", "actor-1", "fixture")
    existing = _entry(_namespace("facade"), "stable", external_refs=(reference,), revision=5)
    existing = TranslationEntry.from_dict({**existing.to_dict(), "provenance": [provenance.to_dict()]})
    collection = TranslationEntryCollection((existing,))
    legacy_rebuild = TranslationEntry(existing.id, existing.key, existing.original, "updated", 1, existing.context)

    with pytest.warns(DeprecationWarning, match="Legacy collection.add"):
        collection.add(legacy_rebuild)
    updated = collection.get(existing.identity)

    assert len(collection) == 1
    assert updated.identity == existing.identity
    assert updated.external_refs == existing.external_refs
    assert updated.provenance == existing.provenance
    assert updated.metadata == existing.metadata
    assert updated.revision == EntryRevision(6)
    assert not hasattr(collection, "_id_index")


def test_legacy_xt_updater_preserves_v2_envelope() -> None:
    reference = ExternalEntryRef("paratranz", "project:7/file:3", 42)
    entry = TranslationEntry(
        id="Editor:00000001|1~INFO:NAM1",
        key="Editor:00000001|1~INFO:NAM1",
        original="Hello",
        translation="",
        stage=0,
        context="INFO:NAM1",
        entry_key=EntryKey(_namespace("xt"), "Editor:00000001|1~INFO:NAM1"),
        external_refs=(reference,),
        metadata=(("plugin.record_type", "INFO:NAM1"),),
    )
    collection = TranslationEntryCollection((entry,))
    xt = XT_Entry(0, "Editor", "INFO:NAM1", "Hello", "你好", 1)

    assert collection.apply_xt_entries((xt,)) == 1
    updated = collection.get(entry.identity)

    assert updated.translation == "你好"
    assert updated.identity == entry.identity
    assert updated.external_refs == entry.external_refs
    assert updated.metadata == entry.metadata
    assert updated.revision == EntryRevision(1)


def test_direct_identity_mutation_is_blocked_and_field_mutation_is_audited() -> None:
    entry = _entry(_namespace("audit"), "one")

    with pytest.raises(AttributeError, match="CollectionMutationPort"):
        entry.key = "other"
    with pytest.warns(DeprecationWarning, match="Direct TranslationEntry.translation"):
        entry.translation = "legacy update"
