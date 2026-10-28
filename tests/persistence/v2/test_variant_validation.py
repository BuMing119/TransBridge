"""Changed-row schema reuse retains complete structural and semantic checks."""

from collections import Counter
from copy import deepcopy
import hashlib
import json
import os

import pytest

from transbridge.persistence.v2 import schema
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef
from transbridge.persistence.v2.models import AtomicWriteError, SchemaValidationError
from transbridge.persistence.v2.repository import VariantRepository
from transbridge.persistence.v2.validated_reads import decode_validated_read, reuse_validated_reads, validated_read

from .fakes import MemoryFilesystem

ROOT = os.path.abspath(os.path.join(os.sep, "transbridge-row-validation"))
REF = VariantRef(VariantId("main"), ProjectId("project"))


def _document():
    return {
        "schema_version": schema.SCHEMA_VERSION,
        "entity_type": "variant",
        "id": REF.identity.value,
        "revision": 2,
        "data": {
            "project_id": REF.project_id.value,
            "translations": {str(index): f"text-{index}" for index in range(6)},
            "labels": {str(index): ["label"] for index in range(6)},
            "label_library": {},
            "source_fingerprints": [{"namespace": "source", "sha256": None}],
            "entries": [
                {
                    "entry_key": {"namespace": "source", "local_key": str(index)},
                    "translation": f"text-{index}",
                    "stage": 0,
                    "labels": [],
                    "provenance": [],
                    "revision": 0,
                    "tombstone": False,
                }
                for index in range(6)
            ],
        },
    }


@pytest.fixture
def records(monkeypatch):
    filesystem = MemoryFilesystem()
    repository = VariantRepository(ROOT, filesystem)
    document = _document()
    filesystem.seed(repository.path_for(REF), schema.serialize_document(document))
    counts = Counter()
    properties = schema.schema_for(REF.kind)["properties"]["data"]["properties"]
    items = schema._PersistenceValidator.VALIDATORS["items"]
    additional = schema._PersistenceValidator.VALIDATORS["additionalProperties"]

    def counted_items(validator, item_schema, instance, parent_schema):
        if parent_schema is properties["entries"] and isinstance(instance, list):
            counts["entries"] += len(instance)
        yield from items(validator, item_schema, instance, parent_schema)

    def counted_additional(validator, item_schema, instance, parent_schema):
        for field in ("translations", "labels"):
            if parent_schema is properties[field] and isinstance(instance, dict):
                counts[field] += len(instance)
        yield from additional(validator, item_schema, instance, parent_schema)

    monkeypatch.setitem(schema._PersistenceValidator.VALIDATORS, "items", counted_items)
    monkeypatch.setitem(schema._PersistenceValidator.VALIDATORS, "additionalProperties", counted_additional)
    return filesystem, repository, document, counts


def _save(repository, document):
    # Construct the DTO directly: repository.save must validate the candidate.
    from transbridge.persistence.v2.models import SchemaEnvelope, VariantDto

    return repository.save(
        REF,
        VariantDto(
            SchemaEnvelope(
                document["schema_version"],
                REF.kind,
                document["id"],
                document["revision"],
                document["data"],
            )
        ),
    )


def test_one_changed_row_checks_only_that_row_and_full_document_semantics(records):
    filesystem, repository, document, counts = records
    path = repository.path_for(REF)
    with reuse_validated_reads():
        repository.load(REF)
        assert counts == {"entries": 6, "translations": 6, "labels": 6}
        counts.clear()
        document["revision"] += 1
        document["data"]["entries"][4]["translation"] = "edited"
        document["data"]["translations"]["4"] = "edited"
        document["data"]["labels"]["4"] = ["changed"]
        written = _save(repository, document)
        assert counts == {"entries": 1, "translations": 1, "labels": 1}
        loaded = repository.load(REF)
        assert loaded.value.envelope.data == document["data"]
        assert loaded.source_hash == written.source_hash == hashlib.sha256(filesystem.read_bytes(path)).hexdigest()
        assert counts == {"entries": 1, "translations": 1, "labels": 1}


def test_cold_write_and_direct_schema_calls_keep_full_row_validation(records):
    _filesystem, repository, document, counts = records
    with reuse_validated_reads():
        _save(repository, document)
        # There was no validated source when the proposal was checked. The
        # destination check subsequently reads and fully validates the source.
        assert counts == {"entries": 12, "translations": 12, "labels": 12}
        counts.clear()
        schema.validate_v2(document, REF)
        assert counts == {"entries": 6, "translations": 6, "labels": 6}


@pytest.mark.parametrize(
    ("damage", "pointer", "code"),
    [
        ("revision_bool", "/data/entries/4/revision", "SCHEMA_VALIDATION_FAILED"),
        ("tombstone_int", "/data/entries/4/tombstone", "SCHEMA_VALIDATION_FAILED"),
        ("unknown_field", "/data/entries/4", "SCHEMA_VALIDATION_FAILED"),
        ("translation_bool", "/data/translations/4", "SCHEMA_VALIDATION_FAILED"),
        ("label_bool", "/data/labels/4/0", "SCHEMA_VALIDATION_FAILED"),
        ("duplicate_entry", "/data/entries/6/entry_key", "DUPLICATE_VARIANT_ENTRY"),
        ("changed_namespace", "/data/entries/4/entry_key/namespace", "UNDECLARED_SOURCE_NAMESPACE"),
        ("removed_namespace", "/data/entries/0/entry_key/namespace", "UNDECLARED_SOURCE_NAMESPACE"),
        ("duplicate_namespace", "/data/source_fingerprints", "DUPLICATE_SOURCE_NAMESPACE"),
        ("bad_top_revision", "/revision", "SCHEMA_VALIDATION_FAILED"),
        ("bad_entries_container", "/data/entries", "SCHEMA_VALIDATION_FAILED"),
        ("bad_labels_container", "/data/labels", "SCHEMA_VALIDATION_FAILED"),
    ],
)
def test_changed_row_reuse_preserves_precise_errors_and_global_invariants(records, damage, pointer, code):
    filesystem, repository, document, counts = records
    path = repository.path_for(REF)
    before = filesystem.read_bytes(path)
    with reuse_validated_reads():
        repository.load(REF)
        counts.clear()
        entry = document["data"]["entries"][4]
        if damage == "revision_bool":
            entry["revision"] = False  # False == 0 must not qualify as identical evidence.
        elif damage == "tombstone_int":
            entry["tombstone"] = 0
        elif damage == "unknown_field":
            entry["unsupported"] = True
        elif damage == "translation_bool":
            document["data"]["translations"]["4"] = True
        elif damage == "label_bool":
            document["data"]["labels"]["4"] = [True]
        elif damage == "duplicate_entry":
            document["data"]["entries"].append(deepcopy(document["data"]["entries"][0]))
        elif damage == "changed_namespace":
            entry["entry_key"]["namespace"] = "missing"
        elif damage == "removed_namespace":
            document["data"]["source_fingerprints"] = []
        elif damage == "duplicate_namespace":
            document["data"]["source_fingerprints"] *= 2
        elif damage == "bad_top_revision":
            document["revision"] = False
        elif damage == "bad_entries_container":
            document["data"]["entries"] = {}
        elif damage == "bad_labels_container":
            document["data"]["labels"] = []
        with pytest.raises(SchemaValidationError) as failure:
            _save(repository, document)
        assert failure.value.code == code
        assert failure.value.pointer == pointer
        assert validated_read(repository, REF, schema.serialize_document(document)) is None
    assert filesystem.read_bytes(path) == before


def test_duplicate_json_key_is_rejected_before_row_reuse(records):
    _filesystem, repository, document, _counts = records
    with reuse_validated_reads():
        repository.load(REF)
        raw = schema.serialize_document(document).replace(b'"revision":0', b'"revision":0,"revision":false', 1)
        with pytest.raises(SchemaValidationError, match="strict UTF-8 JSON"):
            decode_validated_read(repository, REF, raw, validate=schema.validate_v2, reuse_unchanged=True)
        assert validated_read(repository, REF, raw) is None


def test_failed_incremental_write_cannot_seed_target_evidence(records):
    filesystem, repository, document, counts = records
    path = repository.path_for(REF)
    with reuse_validated_reads():
        previous = repository.load(REF)
        counts.clear()
        document["revision"] += 1
        document["data"]["entries"][4]["tombstone"] = True
        filesystem.fail_replace_destinations.add(path)
        with pytest.raises(AtomicWriteError):
            _save(repository, document)
        assert counts["entries"] == 1
        assert validated_read(repository, REF, schema.serialize_document(document)) is None
        assert repository.load(REF).source_hash == previous.source_hash
        filesystem.fail_replace_destinations.clear()
        written = _save(repository, document)
        assert counts["entries"] == 2
        assert repository.load(REF).source_hash == written.source_hash


@pytest.mark.parametrize("field", ["entries", "labels", "translations"])
def test_uncanonicalizable_json_values_never_become_row_reuse_evidence(records, field):
    _filesystem, repository, document, counts = records
    if field == "entries":
        document["data"][field][4]["translation"] = "\ud800"
    elif field == "labels":
        document["data"][field]["4"] = ["\ud800"]
    else:
        document["data"][field]["4"] = "\ud800"
    raw = json.dumps(document, ensure_ascii=True).encode("utf-8")
    with reuse_validated_reads():
        decode_validated_read(repository, REF, raw, validate=schema.validate_v2)
        counts.clear()
        # Even equal values without a canonical encoding must run schema checks.
        document["revision"] += 1
        target = json.dumps(document, ensure_ascii=True).encode("utf-8")
        decode_validated_read(repository, REF, target, validate=schema.validate_v2, reuse_unchanged=True)
        assert counts[field] == 1
        if field == "entries":
            document["data"][field][4]["stage"] = 4
            target = json.dumps(document, ensure_ascii=True).encode("utf-8")
            with pytest.raises(SchemaValidationError) as failure:
                decode_validated_read(repository, REF, target, validate=schema.validate_v2, reuse_unchanged=True)
            assert failure.value.pointer == "/data/entries/4/stage"
            assert validated_read(repository, REF, target) is None
