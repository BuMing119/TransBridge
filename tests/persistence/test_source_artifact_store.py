"""Parsed source artifacts preserve exact baseline and binary dependency closure."""

from dataclasses import replace
import hashlib
import json
import os

import pytest

from tests.persistence.v2.fakes import MemoryFilesystem
from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, ErrorCategory
from transbridge.application.io import FormatId, SourceDescriptor, SourceSnapshot, Stage
from transbridge.application.io.identity import EntryKey, EntryRevision, ExternalEntryRef, Provenance, SourceNamespace
from transbridge.application.io.strings_adapter import LocalizedStringRecord
from transbridge.application.projects.prepared_source_results import prepare_parsed_source
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.persistence import source_artifact_store
from transbridge.persistence.source_artifact_store import (
    ProjectSourceArtifactStore,
    SourceArtifactCorruptError,
    SourceArtifactMissingError,
    SourceArtifactVersionError,
)
from transbridge.persistence.v2.models import PathBoundaryError


def _source(*, content=b"TES4 captured plugin", localized=True):
    namespace = SourceNamespace("source:fixture")
    entry = TranslationEntry(
        "legacy-1",
        "NPC_:00001",
        "Original",
        "",
        0,
        "NPC_:FULL",
        string_id=42,
        entry_key=EntryKey(namespace, "NPC_:00001", "Original"),
        revision=EntryRevision(7),
        external_refs=(ExternalEntryRef("paratranz", "123:456", 42, (("nested", [1, (2,)]),)),),
        provenance=(Provenance("parse", "parser", "fixture", metadata=(("raw", b"provenance bytes"),)),),
        metadata=(("nested", {"tuple": (1, [2, {3: b"metadata bytes"}])}),),
    )
    metadata = ()
    if localized:
        companion = SourceSnapshot.from_bytes(
            SourceDescriptor("bsa:C:/old/Skyrim.bsa!/Strings/Skyrim_English.STRINGS", "Skyrim_English.STRINGS"),
            FormatId.STRINGS,
            b"captured strings",
            metadata=(
                ("strings.storage", "bsa"),
                ("strings.origin_path", "C:/old/Skyrim.bsa"),
                ("strings.member_path", "Strings/Skyrim_English.STRINGS"),
                (
                    "strings.records",
                    (LocalizedStringRecord(0, 42, 0, b"raw text", b"raw text\x00", "raw text", "utf8"),),
                ),
            ),
        )
        loose = SourceSnapshot.from_bytes(
            SourceDescriptor("C:/old/Strings/Skyrim_English.DLSTRINGS"),
            FormatId.DLSTRINGS,
            b"loose strings",
            metadata=(("strings.origin_path", "C:/old/Strings/Skyrim_English.DLSTRINGS"),),
        )
        metadata = (("localized_sources", ((companion, (entry,)), (loose, (entry,)))),)
    snapshot = SourceSnapshot.from_bytes(
        SourceDescriptor("C:/old/Skyrim.esm", "Skyrim.esm"), FormatId.PLUGIN_SSE, content, metadata=metadata
    )
    prepared = prepare_parsed_source(
        "C:/old/Skyrim.esm",
        (entry,),
        snapshot,
        FormatId.PLUGIN_SSE,
        role="migration",
        diagnostics=(Diagnostic("SOURCE_NOTICE", "Retained warning", DiagnosticSeverity.WARNING, ErrorCategory.INPUT),),
    )
    # This is deliberate baseline-only merge state. Re-parsing the entry
    # projections would lose it and would change the baseline translation.
    baseline_entry = replace(
        prepared.baseline.entries[0],
        translation="merged baseline",
        stage=Stage.REVIEWED,
        labels=("baseline-label",),
        inferred_fields=("merge-input",),
        tombstone=True,
    )
    return replace(prepared, baseline=replace(prepared.baseline, entries=(baseline_entry,)))


@pytest.fixture
def storage(tmp_path):
    filesystem = MemoryFilesystem()
    return ProjectSourceArtifactStore(str(tmp_path / "store"), filesystem), filesystem


def _record_path(store, relative):
    return store._path(relative)


def _rewrite_manifest(store, filesystem, artifact_id, mutate):
    old_path = _record_path(store, f"manifests/{artifact_id}.json")
    manifest = json.loads(filesystem.files[old_path])
    mutate(manifest)
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(raw).hexdigest()
    filesystem.seed(_record_path(store, f"manifests/{digest}.json"), raw)
    return digest


def test_roundtrip_preserves_baseline_hydration_role_diagnostics_and_typed_metadata(storage):
    store, filesystem = storage
    prepared = _source()
    artifact_id = store.put(prepared)
    restored = store.get(artifact_id)
    assert restored == prepared
    assert restored.baseline.entries[0].translation == "merged baseline"
    assert restored.hydration.entries[0].translation == ""
    assert restored.to_dict()["role"] == "migration"
    assert store.put(restored) == artifact_id
    assert all("source_artifacts" in path for path in filesystem.files)
    metadata = dict(restored.hydration.entries[0].metadata)["nested"]
    assert metadata == {"tuple": (1, [2, {3: b"metadata bytes"}])}
    assert isinstance(metadata["tuple"], tuple)
    assert isinstance(metadata["tuple"][1], list)


def test_reference_is_not_part_of_its_own_content_identity(storage):
    store, _filesystem = storage
    prepared = _source()
    artifact_id = store.put(prepared)
    referenced = replace(prepared, descriptor=(*prepared.descriptor, ("source_artifact_id", artifact_id)))
    assert store.put(referenced) == artifact_id


def test_binary_payloads_are_separate_and_blob_reads_are_deduplicated(storage):
    store, filesystem = storage
    content = b"large binary cannot occur in json\x00" * 32768
    prepared = _source(content=content)
    artifact_id = store.put(prepared)
    manifest = filesystem.files[_record_path(store, f"manifests/{artifact_id}.json")]
    assert len(manifest) < 20000
    digest = hashlib.sha256(content).hexdigest()
    assert filesystem.files[_record_path(store, f"blobs/{digest}.bin")] is content
    filesystem.calls.clear()
    assert store.get(artifact_id) == prepared
    reads = [path for operation, path in filesystem.calls if operation == "read"]
    assert len(reads) == len(set(reads))
    assert all("source_artifacts" in path for path in reads)


def test_relocation_rebases_nested_loose_and_bsa_sources_without_live_access(storage):
    store, filesystem = storage
    prepared = _source()
    artifact_id = store.put(prepared)
    original_records = dict(filesystem.files)
    filesystem.calls.clear()
    restored = store.get(artifact_id, location="D:/moved/Skyrim.esm")
    assert restored.to_dict()["location"] == "D:/moved/Skyrim.esm"
    assert restored.to_dict()["path"] == "D:/moved/Skyrim.esm"
    assert restored.hydration.location == "D:/moved/Skyrim.esm"
    assert restored.hydration.source_snapshot.source.uri == "D:/moved/Skyrim.esm"
    companion, loose = dict(restored.hydration.source_snapshot.metadata)["localized_sources"]
    assert companion[0].source.uri == "bsa:D:\\moved\\Skyrim.bsa!/Strings/Skyrim_English.STRINGS"
    assert dict(companion[0].metadata)["strings.origin_path"] == "D:\\moved\\Skyrim.bsa"
    assert loose[0].source.uri == "D:\\moved\\Strings\\Skyrim_English.DLSTRINGS"
    assert restored.baseline == prepared.baseline
    assert filesystem.files == original_records
    assert all("source_artifacts" in path for _operation, path in filesystem.calls)


@pytest.mark.parametrize("identity", ["../outside", "A" * 64, "a" * 63, "a" * 64 + "/x"])
def test_rejects_non_hash_artifact_ids_before_io(storage, identity):
    store, filesystem = storage
    with pytest.raises(ValueError, match="SHA-256"):
        store.get(identity)
    assert not filesystem.calls


def test_missing_manifest_and_missing_dependency_are_distinct(storage):
    store, filesystem = storage
    with pytest.raises(SourceArtifactMissingError):
        store.get("0" * 64)
    artifact_id = store.put(_source())
    manifest = json.loads(filesystem.files[_record_path(store, f"manifests/{artifact_id}.json")])
    filesystem.files.pop(_record_path(store, f"blobs/{manifest['blobs'][0]}.bin"))
    with pytest.raises(SourceArtifactCorruptError, match="dependency is missing"):
        store.get(artifact_id)


def test_corrupt_blob_and_manifest_are_rejected(storage):
    store, filesystem = storage
    artifact_id = store.put(_source())
    manifest_path = _record_path(store, f"manifests/{artifact_id}.json")
    manifest = json.loads(filesystem.files[manifest_path])
    blob_path = _record_path(store, f"blobs/{manifest['blobs'][0]}.bin")
    filesystem.seed(blob_path, b"corrupted")
    with pytest.raises(SourceArtifactCorruptError, match="blob digest mismatch"):
        store.get(artifact_id)
    filesystem.seed(manifest_path, b"{}")
    with pytest.raises(SourceArtifactCorruptError, match="manifest digest mismatch"):
        store.get(artifact_id)


@pytest.mark.parametrize("change", ["abi", "closure", "tag"])
def test_rejects_incompatible_or_unsafe_manifest_contracts(storage, change):
    store, filesystem = storage
    artifact_id = store.put(_source())

    def mutate(manifest):
        if change == "abi":
            manifest["abi"] += 1
        elif change == "closure":
            manifest["blobs"].append("0" * 64)
        else:
            manifest["prepared"]["$type"] = "subprocess.Popen"

    artifact_id = _rewrite_manifest(store, filesystem, artifact_id, mutate)
    error = SourceArtifactVersionError if change == "abi" else SourceArtifactCorruptError
    with pytest.raises(error):
        store.get(artifact_id)


def test_shared_blob_digest_is_validated_once_per_recovery(storage, monkeypatch):
    store, filesystem = storage
    artifact_id = store.put(_source())
    manifest = json.loads(filesystem.files[_record_path(store, f"manifests/{artifact_id}.json")])
    validated = []
    original = source_artifact_store._validate_digest

    def validate(value):
        validated.append(value)
        original(value)

    monkeypatch.setattr(source_artifact_store, "_validate_digest", validate)
    assert store.get(artifact_id) == _source()
    assert validated == [artifact_id, *manifest["blobs"]]


@pytest.mark.parametrize("digest", ["0" * 64, "../outside", "A" * 64, None, ["0" * 64]])
def test_undeclared_blob_references_are_rejected_before_dependency_io(storage, digest):
    store, filesystem = storage
    artifact_id = store.put(_source())

    def mutate(manifest):
        manifest["prepared"] = {"$blob": digest}

    artifact_id = _rewrite_manifest(store, filesystem, artifact_id, mutate)
    filesystem.calls.clear()
    with pytest.raises(SourceArtifactCorruptError, match="closure"):
        store.get(artifact_id)
    reads = [path for operation, path in filesystem.calls if operation == "read"]
    assert reads == [_record_path(store, f"manifests/{artifact_id}.json")]


def test_unused_declared_blob_is_rejected_by_recovery_and_export(storage):
    store, filesystem = storage
    artifact_id = store.put(_source())

    def mutate(manifest):
        manifest["blobs"] = sorted([*manifest["blobs"], "0" * 64])

    artifact_id = _rewrite_manifest(store, filesystem, artifact_id, mutate)
    with pytest.raises(SourceArtifactCorruptError, match="closure"):
        store.get(artifact_id)
    with pytest.raises(SourceArtifactCorruptError):
        tuple(store.iter_records(artifact_id))


@pytest.mark.parametrize(
    "node",
    [
        {"$blob": "0" * 64, "extra": None},
        {"$blob": "0" * 64, "offset": 0},
        {"$blob": "0" * 64, "offset": 0, "size": 1, "extra": None},
        {"$tuple": [], "extra": None},
        {"$tuple": {}},
        {"$mapping": [], "extra": None},
        {"$mapping": {}},
        {"$enum": "Stage"},
        {"$enum": "Stage", "value": 0, "extra": None},
        {"$type": "SourceNamespace"},
        {"$type": "SourceNamespace", "fields": {"value": "source:test"}, "extra": None},
        {"$type": "SourceNamespace", "fields": {}},
        {"$type": "SourceNamespace", "fields": {"value": "source:test", "extra": None}},
        {"$mapping": [["same", 1], ["same", 2]]},
    ],
)
def test_tag_dispatch_keeps_exact_shapes_and_duplicate_mapping_checks(storage, node):
    store, filesystem = storage
    artifact_id = store.put(_source())

    def mutate(manifest):
        manifest["prepared"]["fields"]["diagnostics"] = node

    artifact_id = _rewrite_manifest(store, filesystem, artifact_id, mutate)
    with pytest.raises(SourceArtifactCorruptError):
        store.get(artifact_id)


def test_duplicate_json_keys_remain_invalid(storage):
    store, filesystem = storage
    artifact_id = store.put(_source())
    raw = filesystem.files[_record_path(store, f"manifests/{artifact_id}.json")]
    raw = raw[:-1] + b',"location":"duplicate"}'
    artifact_id = hashlib.sha256(raw).hexdigest()
    filesystem.seed(_record_path(store, f"manifests/{artifact_id}.json"), raw)
    with pytest.raises(SourceArtifactCorruptError, match="manifest is invalid"):
        store.get(artifact_id)


def test_source_snapshot_without_captured_content_cannot_enter_an_artifact(storage):
    store, filesystem = storage
    prepared = _source(localized=False)
    snapshot = replace(prepared.hydration.source_snapshot, content=None, lease_id="ephemeral")
    prepared = replace(prepared, hydration=replace(prepared.hydration, source_snapshot=snapshot))
    with pytest.raises(ValueError, match="captured bytes"):
        store.put(prepared)
    assert not filesystem.files


def test_publication_failure_leaves_no_visible_manifest_or_staging_file(storage):
    store, filesystem = storage
    prepared = _source(content=b"large plugin" * 10000)
    digest = hashlib.sha256(prepared.hydration.source_snapshot.content).hexdigest()
    filesystem.fail_durable_replace_destinations.add(_record_path(store, f"blobs/{digest}.bin"))
    with pytest.raises(OSError, match="replace fault"):
        store.put(prepared)
    assert not any(os.sep + "manifests" + os.sep in path or path.endswith(".tmp") for path in filesystem.files)


def test_read_fault_is_not_reported_as_missing_or_swallowed(storage):
    store, filesystem = storage
    artifact_id = store.put(_source())
    filesystem.fail_read_paths.add(_record_path(store, f"manifests/{artifact_id}.json"))
    with pytest.raises(OSError, match="read fault"):
        store.get(artifact_id)


def test_archive_exports_only_requested_closure_and_imports_without_local_dependencies(storage, tmp_path):
    store, filesystem = storage
    prepared = _source()
    artifact_id = store.put(prepared)
    other_id = store.put(_source(content=b"other source", localized=False))
    records = dict(store.iter_records(artifact_id))
    assert f"manifests/{artifact_id}.json" in records
    assert f"manifests/{other_id}.json" not in records
    assert all(path.startswith(("manifests/", "blobs/")) for path in records)
    assert ProjectSourceArtifactStore.read_records(artifact_id, records.__getitem__) == prepared
    imported_filesystem = MemoryFilesystem()
    imported = ProjectSourceArtifactStore(str(tmp_path / "imported"), imported_filesystem)
    imported.import_records(artifact_id, records.__getitem__)
    assert imported.get(artifact_id) == prepared
    replacements = [path for operation, path in imported_filesystem.calls if operation == "replace-durable"]
    assert replacements[-1] == _record_path(imported, f"manifests/{artifact_id}.json")
    assert set(records.values()).issubset(set(filesystem.files.values()))


def test_import_requires_whole_external_closure_even_when_local_artifact_exists(storage):
    store, filesystem = storage
    artifact_id = store.put(_source())
    records = dict(store.iter_records(artifact_id))
    records.pop(next(path for path in records if path.startswith("blobs/")))
    filesystem.calls.clear()
    with pytest.raises(SourceArtifactCorruptError, match="dependency is missing"):
        store.import_records(artifact_id, records.__getitem__)
    assert not filesystem.calls


def test_recovered_mutable_companion_metadata_does_not_pollute_later_reads(storage):
    store, _filesystem = storage
    artifact_id = store.put(_source())
    restored = store.get(artifact_id)
    companion = dict(restored.hydration.source_snapshot.metadata)["localized_sources"][0][1][0]
    dict(companion.metadata)["nested"]["tuple"][1].append("mutated")
    assert store.get(artifact_id) == _source()


def test_many_small_record_bytes_are_packed_and_preserve_each_slice(storage):
    store, filesystem = storage
    prepared = _source(content=b"large plugin" * 10000, localized=False)
    records = tuple((index.to_bytes(4, "little") + b"-raw\x00") for index in range(4000))
    snapshot = replace(prepared.hydration.source_snapshot, metadata=(("records", records),))
    prepared = replace(prepared, hydration=replace(prepared.hydration, source_snapshot=snapshot))
    artifact_id = store.put(prepared)
    manifest = json.loads(filesystem.files[_record_path(store, f"manifests/{artifact_id}.json")])
    assert len(manifest["blobs"]) == 2
    assert store.get(artifact_id) == prepared


def test_small_binary_packs_cross_size_boundaries_without_losing_references(storage):
    store, filesystem = storage
    prepared = _source(localized=False)
    records = tuple(index.to_bytes(4, "little") + b"x" * 65532 for index in range(132))
    snapshot = replace(prepared.hydration.source_snapshot, metadata=(("records", (*records, records[0])),))
    prepared = replace(prepared, hydration=replace(prepared.hydration, source_snapshot=snapshot))
    artifact_id = store.put(prepared)
    manifest = json.loads(filesystem.files[_record_path(store, f"manifests/{artifact_id}.json")])
    assert len(manifest["blobs"]) == 2
    assert all(
        len(filesystem.files[_record_path(store, f"blobs/{digest}.bin")]) <= 8 * 1024 * 1024
        for digest in manifest["blobs"]
    )
    assert store.get(artifact_id) == prepared


def test_packed_binary_slices_cannot_exceed_their_verified_blob(storage):
    store, filesystem = storage
    artifact_id = store.put(_source())

    def mutate(manifest):
        def visit(value):
            if isinstance(value, dict):
                if "offset" in value and "$blob" in value:
                    value["offset"] = -1
                else:
                    for child in value.values():
                        visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(manifest["prepared"])

    artifact_id = _rewrite_manifest(store, filesystem, artifact_id, mutate)
    with pytest.raises(SourceArtifactCorruptError):
        store.get(artifact_id)


def test_manifest_publication_failure_never_becomes_a_successful_get(storage):
    store, filesystem = storage
    prepared = _source()
    artifact_id = store.put(prepared)
    manifest_path = _record_path(store, f"manifests/{artifact_id}.json")
    filesystem.files.pop(manifest_path)
    filesystem.fail_durable_replace_destinations.add(manifest_path)
    with pytest.raises(OSError, match="replace fault"):
        store.put(prepared)
    with pytest.raises(SourceArtifactMissingError):
        store.get(artifact_id)
    assert not any(path.endswith(".tmp") for path in filesystem.files)


def test_existing_immutable_record_is_never_silently_replaced(storage):
    store, filesystem = storage
    prepared = _source()
    artifact_id = store.put(prepared)
    manifest_path = _record_path(store, f"manifests/{artifact_id}.json")
    filesystem.seed(manifest_path, b"corrupted immutable manifest")
    with pytest.raises(SourceArtifactCorruptError, match="immutable"):
        store.put(prepared)
    assert filesystem.files[manifest_path] == b"corrupted immutable manifest"


def test_artifact_paths_reject_filesystem_aliases_outside_root(storage, tmp_path):
    store, filesystem = storage
    filesystem.canonical_aliases[store._paths.root] = str(tmp_path / "outside")
    with pytest.raises(PathBoundaryError, match="root"):
        store.put(_source())
    assert not filesystem.files


def test_os_filesystem_smoke_uses_only_artifact_files(tmp_path):
    store = ProjectSourceArtifactStore(str(tmp_path / "actual-store"))
    prepared = _source()
    artifact_id = store.put(prepared)
    assert store.get(artifact_id) == prepared
    assert all("source_artifacts" in str(path) for path in (tmp_path / "actual-store").rglob("*") if path.is_file())
