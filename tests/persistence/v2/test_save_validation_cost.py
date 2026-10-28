"""Identical preimages can reuse validation, but filesystem changes cannot."""

from collections import Counter
from dataclasses import replace

import pytest

from transbridge.application.contracts import DomainError
from transbridge.persistence.v2 import repository
from transbridge.persistence.v2.lifecycle_transactions import ProjectLifecycleTransactionStore
from transbridge.persistence.v2.schema import serialize_document
from transbridge.persistence.v2.variant import VariantSnapshot

from .fakes import MemoryFilesystem
from .test_lifecycle_save_transactions import PROJECT_REF, ROOT, VARIANT_REF, _commit, _save, _seed, _variant


def test_single_document_save_validates_old_variant_once_and_verifies_published_bytes(monkeypatch):
    filesystem = MemoryFilesystem()
    projects, variants = _seed(filesystem)
    store = ProjectLifecycleTransactionStore(ROOT, filesystem, projects, variants)
    validations = Counter()
    validate = repository.validate_v2

    def counted(document, ref):
        if ref == VARIANT_REF:
            validations[document["revision"]] += 1
        return validate(document, ref)

    monkeypatch.setattr(repository, "validate_v2", counted)
    before = len(filesystem.calls)
    _commit(store, "validation-cost", _save(project_revision=3))

    assert validations == {2: 1, 3: 1}  # preimage and target; readback still checks the actual bytes below
    variant_path = variants.path_for(VARIANT_REF)
    calls = filesystem.calls[before:]
    assert sum(operation == "read" and path == variant_path for operation, path in calls) == 4
    assert sum(operation == "replace" and path == variant_path for operation, path in calls) == 1
    assert variants.load(VARIANT_REF).value.envelope.revision == 3


def test_same_revision_external_change_is_revalidated_and_refused(monkeypatch):
    filesystem = MemoryFilesystem()
    projects, variants = _seed(filesystem)
    store = ProjectLifecycleTransactionStore(ROOT, filesystem, projects, variants)
    save = variants.save_if_revision
    altered = _variant(2).to_dto()
    data = dict(altered.envelope.data, label_library={"external": {"name": "External"}})
    external = serialize_document(replace(altered, envelope=replace(altered.envelope, data=data)).envelope.to_dict())
    variant_path = variants.path_for(VARIANT_REF)

    def change_before_conditional_write(*args, **kwargs):
        filesystem.seed(variant_path, external)
        return save(*args, **kwargs)

    monkeypatch.setattr(variants, "save_if_revision", change_before_conditional_write)
    with pytest.raises(DomainError) as failure:
        _commit(store, "same-revision-change", _save(project_revision=3))
    assert failure.value.code == "ACTIVE_SAVE_PERSISTED_STALE"
    assert filesystem.read_bytes(variant_path) == external


def test_validation_cache_does_not_cross_save_transactions(monkeypatch):
    filesystem = MemoryFilesystem()
    projects, variants = _seed(filesystem)
    store = ProjectLifecycleTransactionStore(ROOT, filesystem, projects, variants)
    _commit(store, "first-save", _save(project_revision=3))
    validate = repository.validate_v2
    validated = []

    def counted(document, ref):
        if ref == VARIANT_REF:
            validated.append(document["revision"])
        return validate(document, ref)

    monkeypatch.setattr(repository, "validate_v2", counted)
    _commit(store, "second-save", _save(project_revision=3, variant_revision=4, expected_variant_revision=3))
    assert validated == [3, 4]


def test_two_document_save_serializes_variant_target_once_for_journal_and_publication(monkeypatch):
    filesystem = MemoryFilesystem()
    projects, variants = _seed(filesystem)
    store = ProjectLifecycleTransactionStore(ROOT, filesystem, projects, variants)
    pending = _save()
    serialized = []
    serialize = VariantSnapshot.to_dto

    def counted(snapshot):
        serialized.append(snapshot)
        return serialize(snapshot)

    monkeypatch.setattr(VariantSnapshot, "to_dto", counted)
    _commit(store, "one-target-serialization", pending)

    assert serialized == [pending.variant]
    assert projects.load(PROJECT_REF).value.envelope.revision == 4
    assert variants.load(VARIANT_REF).value.envelope.revision == 3
