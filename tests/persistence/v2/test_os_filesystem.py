"""Real temporary-filesystem success-path evidence for the V2 adapter."""

from __future__ import annotations

import os
from pathlib import Path

from transbridge.persistence.v2 import (
    SCHEMA_VERSION,
    LoadedRecord,
    OsPersistenceFilesystem,
    ProjectDto,
    ProjectId,
    ProjectRef,
    ProjectRepository,
    SchemaEnvelope,
)

FIXTURE = Path(__file__).with_name("fixtures") / "project-v1.json"


def _dto(ref: ProjectRef) -> ProjectDto:
    return ProjectDto(
        SchemaEnvelope(
            SCHEMA_VERSION,
            ref.kind,
            ref.identity.value,
            1,
            {
                "name": "真实临时项目",
                "sources": [],
                "variant_ids": [],
                "active_variant_id": None,
            },
        )
    )


def test_real_temp_filesystem_v2_round_trip(tmp_path: Path) -> None:
    filesystem = OsPersistenceFilesystem()
    root = str(tmp_path / "非ASCII-持久化")
    ref = ProjectRef(ProjectId("project-real"))
    repository = ProjectRepository(root, filesystem)

    repository.save(ref, _dto(ref))
    loaded = repository.load(ref)

    assert isinstance(loaded, LoadedRecord)
    assert loaded.value == _dto(ref)
    assert filesystem.exists(repository.path_for(ref))


def test_real_temp_filesystem_migrates_fixture_with_verified_backup(tmp_path: Path) -> None:
    filesystem = OsPersistenceFilesystem()
    root = str(tmp_path / "migration-fixture")
    ref = ProjectRef(ProjectId("project-real"))
    repository = ProjectRepository(root, filesystem)
    source_path = repository.path_for(ref)
    filesystem.make_dirs(os.path.dirname(source_path))
    filesystem.write_bytes(source_path, FIXTURE.read_bytes())

    loaded = repository.load(ref)

    assert isinstance(loaded, LoadedRecord)
    assert loaded.migrated is True
    assert loaded.migration_report is not None
    assert filesystem.read_bytes(loaded.migration_report.backup_path) == FIXTURE.read_bytes()
