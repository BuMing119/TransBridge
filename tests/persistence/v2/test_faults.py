"""Fault injection contracts for migration, backup, quarantine and save."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from transbridge.persistence.v2 import (
    SCHEMA_VERSION,
    AtomicWriteError,
    BackupVerificationError,
    ProjectDto,
    ProjectId,
    ProjectRef,
    ProjectRepository,
    QuarantineResult,
    RepositoryPaths,
    SchemaEnvelope,
)

from .fakes import MemoryFilesystem

ROOT = os.path.abspath(os.path.join(os.sep, "transbridge-v2-faults"))
FIXTURE = Path(__file__).with_name("fixtures") / "project-v1.json"


def _v1() -> bytes:
    return FIXTURE.read_bytes()


def _dto(revision: int, name: str) -> ProjectDto:
    ref = ProjectRef(ProjectId("project-1"))
    return ProjectDto(
        SchemaEnvelope(
            SCHEMA_VERSION,
            ref.kind,
            ref.identity.value,
            revision,
            {"name": name, "sources": [], "variant_ids": [], "active_variant_id": None},
        )
    )


def test_backup_replace_fault_stops_migration_and_preserves_original() -> None:
    filesystem = MemoryFilesystem()
    ref = ProjectRef(ProjectId("project-1"))
    repo = ProjectRepository(ROOT, filesystem)
    raw = _v1()
    source_path = repo.path_for(ref)
    filesystem.seed(source_path, raw)
    digest = hashlib.sha256(raw).hexdigest()
    backup_path = RepositoryPaths(ROOT, filesystem).backup(ref, digest, 1)
    filesystem.fail_replace_destinations.add(backup_path)

    with pytest.raises(AtomicWriteError):
        repo.load(ref)

    assert filesystem.read_bytes(source_path) == raw
    assert backup_path not in filesystem.files


def test_migration_replace_fault_preserves_original_after_verified_backup() -> None:
    filesystem = MemoryFilesystem()
    ref = ProjectRef(ProjectId("project-1"))
    repo = ProjectRepository(ROOT, filesystem)
    raw = _v1()
    source_path = repo.path_for(ref)
    filesystem.seed(source_path, raw)
    filesystem.fail_replace_destinations.add(source_path)

    with pytest.raises(AtomicWriteError):
        repo.load(ref)

    digest = hashlib.sha256(raw).hexdigest()
    backup_path = RepositoryPaths(ROOT, filesystem).backup(ref, digest, 1)
    assert filesystem.read_bytes(source_path) == raw
    assert filesystem.read_bytes(backup_path) == raw
    assert not any(".tmp" in path for path in filesystem.files)


def test_existing_backup_with_wrong_content_blocks_migration() -> None:
    filesystem = MemoryFilesystem()
    ref = ProjectRef(ProjectId("project-1"))
    repo = ProjectRepository(ROOT, filesystem)
    raw = _v1()
    source_path = repo.path_for(ref)
    filesystem.seed(source_path, raw)
    digest = hashlib.sha256(raw).hexdigest()
    backup_path = RepositoryPaths(ROOT, filesystem).backup(ref, digest, 1)
    filesystem.seed(backup_path, b"not-the-source")

    with pytest.raises(BackupVerificationError):
        repo.load(ref)

    assert filesystem.read_bytes(source_path) == raw
    assert filesystem.read_bytes(backup_path) == b"not-the-source"


def test_backup_verification_read_fault_cleans_backup_and_preserves_source() -> None:
    filesystem = MemoryFilesystem()
    ref = ProjectRef(ProjectId("project-1"))
    repo = ProjectRepository(ROOT, filesystem)
    raw = _v1()
    source_path = repo.path_for(ref)
    filesystem.seed(source_path, raw)
    digest = hashlib.sha256(raw).hexdigest()
    backup_path = RepositoryPaths(ROOT, filesystem).backup(ref, digest, 1)
    filesystem.fail_read_paths.add(backup_path)

    with pytest.raises(OSError, match="injected read fault"):
        repo.load(ref)

    filesystem.fail_read_paths.clear()
    assert filesystem.read_bytes(source_path) == raw
    assert backup_path not in filesystem.files


def test_quarantine_report_fault_removes_partial_copy_and_retains_source() -> None:
    filesystem = MemoryFilesystem()
    ref = ProjectRef(ProjectId("project-1"))
    repo = ProjectRepository(ROOT, filesystem)
    raw = b'{"name":'
    source_path = repo.path_for(ref)
    filesystem.seed(source_path, raw)
    digest = hashlib.sha256(raw).hexdigest()
    paths = RepositoryPaths(ROOT, filesystem)
    payload_path = paths.quarantine_payload(ref, digest)
    report_path = paths.quarantine_report(ref, digest)
    filesystem.fail_replace_destinations.add(report_path)

    with pytest.raises(AtomicWriteError):
        repo.load(ref)

    assert filesystem.read_bytes(source_path) == raw
    assert payload_path not in filesystem.files
    assert report_path not in filesystem.files


def test_quarantine_is_repeatable_and_never_replaces_invalid_source() -> None:
    filesystem = MemoryFilesystem()
    ref = ProjectRef(ProjectId("project-1"))
    repo = ProjectRepository(ROOT, filesystem)
    raw = json.dumps({"name": 7}).encode()
    source_path = repo.path_for(ref)
    filesystem.seed(source_path, raw)

    first = repo.load(ref)
    first_files = dict(filesystem.files)
    second = repo.load(ref)

    assert isinstance(first, QuarantineResult)
    assert isinstance(second, QuarantineResult)
    assert first.quarantine == second.quarantine
    assert filesystem.files == first_files
    assert filesystem.read_bytes(source_path) == raw


def test_save_replace_fault_does_not_modify_existing_v2_record() -> None:
    filesystem = MemoryFilesystem()
    ref = ProjectRef(ProjectId("project-1"))
    repo = ProjectRepository(ROOT, filesystem)
    repo.save(ref, _dto(1, "Original"))
    source_path = repo.path_for(ref)
    original = filesystem.read_bytes(source_path)
    filesystem.fail_replace_destinations.add(source_path)

    with pytest.raises(AtomicWriteError):
        repo.save(ref, _dto(2, "Changed"))

    assert filesystem.read_bytes(source_path) == original
    assert not any(".tmp" in path for path in filesystem.files)
