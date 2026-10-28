"""Validation evidence is local, exact-byte based and isolated from mutable DTOs."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path

import pytest

from transbridge.application.projects.models import LifecycleActivation
from transbridge.persistence.v2 import project_activation_journal, project_save_journal, repository
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef
from transbridge.persistence.v2.lifecycle_transactions import ProjectLifecycleTransactionStore
from transbridge.persistence.v2.models import AtomicWriteError, QuarantineResult, SchemaValidationError
from transbridge.persistence.v2.schema import serialize_document
from transbridge.persistence.v2.validated_reads import ValidatedReads, reuse_validated_reads, validated_read

from .fakes import MemoryFilesystem
from .test_lifecycle_save_transactions import PROJECT_REF, ROOT, VARIANT_REF, _commit, _save, _seed, _variant


@pytest.fixture
def records(monkeypatch):
    filesystem = MemoryFilesystem()
    projects, variants = _seed(filesystem)
    validations = Counter()
    validate = repository.validate_v2

    def counted(document, ref):
        validations[ref, document["revision"]] += 1
        return validate(document, ref)

    monkeypatch.setattr(repository, "validate_v2", counted)
    monkeypatch.setattr(project_save_journal, "validate_v2", counted)
    monkeypatch.setattr(project_activation_journal, "validate_v2", counted)
    return filesystem, projects, variants, validations


def test_snapshot_load_and_nested_scopes_share_validation_with_detached_data(records):
    filesystem, _projects, variants, validations = records
    path = variants.path_for(VARIANT_REF)
    raw = filesystem.read_bytes(path)
    reads_before = len(filesystem.calls)
    with reuse_validated_reads() as evidence:
        snapshot = variants.read_snapshot_record(VARIANT_REF)
        snapshot.value.envelope.data["translations"]["pollution"] = []
        with reuse_validated_reads() as nested:
            assert nested is evidence
            loaded = variants.load(VARIANT_REF)
        assert "pollution" not in loaded.value.envelope.data["translations"]
        loaded.value.envelope.data["labels"]["pollution"] = ["bad"]
        assert "pollution" not in variants.read_snapshot(VARIANT_REF).envelope.data["labels"]
        with pytest.raises(SchemaValidationError):
            variants.save(VARIANT_REF, snapshot.value)
    assert snapshot.source_hash == hashlib.sha256(raw).hexdigest()
    assert validations[VARIANT_REF, 2] == 2  # exact source once, mutated invalid proposal once
    assert sum(call == ("read", path) for call in filesystem.calls[reads_before:]) == 3
    assert filesystem.read_bytes(path) == raw


def test_default_scopes_and_repository_instances_do_not_share_evidence(records):
    filesystem, _projects, variants, validations = records
    with reuse_validated_reads():
        variants.load(VARIANT_REF)
    with reuse_validated_reads():
        variants.load(VARIANT_REF)
        second = repository.VariantRepository(ROOT, filesystem)
        second.load(VARIANT_REF)
    variants.load(VARIANT_REF)
    assert validations[VARIANT_REF, 2] == 4


def test_explicit_evidence_crosses_threads_without_sharing_mutable_dtos(records):
    _filesystem, _projects, variants, validations = records
    evidence = ValidatedReads()
    with reuse_validated_reads(evidence):
        original = variants.read_snapshot(VARIANT_REF)

    def read():
        with reuse_validated_reads(evidence):
            return variants.load(VARIANT_REF).value

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _index: read(), range(2)))
    first.envelope.data["translations"]["pollution"] = "changed"
    assert first is not second and second is not original
    assert "pollution" not in second.envelope.data["translations"]
    assert "pollution" not in original.envelope.data["translations"]
    assert validations[VARIANT_REF, 2] == 1
    variants.load(VARIANT_REF)
    assert validations[VARIANT_REF, 2] == 2


def test_concurrent_cold_reads_share_only_explicit_evidence(records):
    _filesystem, _projects, variants, validations = records
    evidence = ValidatedReads()

    def read(shared):
        with reuse_validated_reads(shared):
            return variants.read_snapshot(VARIANT_REF)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(read, (evidence, evidence)))
        assert first == second and first is not second
        assert validations[VARIANT_REF, 2] == 1
        list(executor.map(read, (None, None)))
        assert validations[VARIANT_REF, 2] == 3


def test_same_revision_changed_and_corrupt_bytes_are_validated_again(records):
    filesystem, _projects, variants, validations = records
    path = variants.path_for(VARIANT_REF)
    with reuse_validated_reads():
        original = variants.load(VARIANT_REF)
        document = original.value.envelope.to_dict()
        document["data"]["translations"]["external"] = "external edit"
        changed = serialize_document(document)
        filesystem.seed(path, changed)
        assert variants.load(VARIANT_REF).source_hash == hashlib.sha256(changed).hexdigest()
        document["data"]["translations"]["external"] = []
        damaged = serialize_document(document)
        filesystem.seed(path, damaged)
        assert isinstance(variants.load(VARIANT_REF), QuarantineResult)
        assert isinstance(variants.load(VARIANT_REF), QuarantineResult)
        assert validated_read(variants, VARIANT_REF, damaged) is None
    # Failed schema checks are retried after recovery; no failed result becomes evidence.
    assert validations[VARIANT_REF, 2] == 6
    assert filesystem.read_bytes(path) == damaged


def test_failed_save_does_not_remember_unpublished_target(records):
    filesystem, _projects, variants, validations = records
    path = variants.path_for(VARIANT_REF)
    target = _variant(3).to_dto()
    raw = serialize_document(target.envelope.to_dict())
    with reuse_validated_reads():
        before = variants.load(VARIANT_REF)
        filesystem.fail_replace_destinations.add(path)
        with pytest.raises(AtomicWriteError):
            variants.save(VARIANT_REF, target)
        assert validated_read(variants, VARIANT_REF, raw) is None
        assert variants.load(VARIANT_REF) == before
        filesystem.fail_replace_destinations.clear()
        variants.save(VARIANT_REF, target)
        assert variants.load(VARIANT_REF).value.envelope.revision == 3
    assert validations[VARIANT_REF, 3] == 2


def test_legacy_snapshot_evidence_does_not_bypass_durable_migration():
    filesystem = MemoryFilesystem()
    variants = repository.VariantRepository(ROOT, filesystem)
    ref = VariantRef(VariantId("main"), ProjectId("project-1"))
    raw = (Path(__file__).with_name("fixtures") / "variant-v1.json").read_bytes()
    path = variants.path_for(ref)
    filesystem.seed(path, raw)
    before = dict(filesystem.files)
    with reuse_validated_reads():
        snapshot = variants.read_snapshot_record(ref)
        assert snapshot.migrated and snapshot.migration_report is None
        assert snapshot.source_hash == hashlib.sha256(raw).hexdigest()
        assert filesystem.files == before
        assert validated_read(variants, ref, raw) is None
        loaded = variants.load(ref)
        assert loaded.migrated
        assert filesystem.read_bytes(loaded.migration_report.backup_path) == raw
        current = filesystem.read_bytes(path)
        assert json.loads(current)["schema_version"] == repository.SCHEMA_VERSION
        assert validated_read(variants, ref, current) is not None
        assert variants.load(ref).value == loaded.value


def test_two_document_save_reuses_preimages_and_targets_across_manifest_and_repositories(records):
    filesystem, projects, variants, validations = records
    store = ProjectLifecycleTransactionStore(ROOT, filesystem, projects, variants)
    before = len(filesystem.calls)
    _commit(store, "unified-manifest-validation", _save())
    assert validations == {(PROJECT_REF, 3): 1, (PROJECT_REF, 4): 1, (VARIANT_REF, 2): 1, (VARIANT_REF, 3): 1}
    for repo, ref, revision in ((projects, PROJECT_REF, 4), (variants, VARIANT_REF, 3)):
        path = repo.path_for(ref)
        assert sum(call == ("read", path) for call in filesystem.calls[before:]) >= 4
        assert json.loads(filesystem.read_bytes(path))["revision"] == revision


def test_activation_reuses_prepared_record_validation_and_manifest_payloads(records):
    filesystem, projects, variants, validations = records
    protocol = project_activation_journal.ProjectActivationProtocol(ROOT, filesystem, projects, variants)
    evidence = ValidatedReads()
    with reuse_validated_reads(evidence):
        project = projects.read_snapshot_record(PROJECT_REF)
        variant = variants.read_snapshot_record(VARIANT_REF)
    activation = LifecycleActivation(
        None,
        None,
        None,
        None,
        _save().project,
        VARIANT_REF,
        _variant(3),
        None,
        expected_project_revision=3,
        expected_variant_revision=2,
        expected_project_hash=project.source_hash,
        expected_variant_hash=variant.source_hash,
    )
    with reuse_validated_reads(evidence):
        protocol.commit(activation, "unified-activation-validation")
        assert projects.load(PROJECT_REF).value.envelope.revision == 4
        assert variants.load(VARIANT_REF).value.envelope.revision == 3
    assert validations == {(PROJECT_REF, 3): 1, (PROJECT_REF, 4): 1, (VARIANT_REF, 2): 1, (VARIANT_REF, 3): 1}
