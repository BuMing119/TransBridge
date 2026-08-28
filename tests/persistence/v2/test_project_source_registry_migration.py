from __future__ import annotations

import hashlib
import json
import os

from transbridge.persistence.v2 import (
    LoadedRecord,
    ProjectDto,
    ProjectId,
    ProjectRef,
    ProjectRepository,
    QuarantineResult,
    SchemaEnvelope,
)

from .fakes import MemoryFilesystem

ROOT = os.path.abspath(os.path.join(os.sep, "transbridge-source-registry-migration"))


def _v2_document(*, format_id: str = "plugin.sse") -> dict[str, object]:
    return {
        "schema_version": 2,
        "entity_type": "project",
        "id": "project-1",
        "revision": 4,
        "data": {
            "name": "Project",
            "sources": [
                {
                    "source_id": "f" * 64,
                    "format_id": format_id,
                    "location": "C:/mods/base.esp",
                    "fingerprint": "f" * 64,
                    "role": "primary",
                },
                {
                    "source_id": "legacy:xml",
                    "format_id": "xml.eet",
                    "location": "C:/mods/base.xml",
                    "fingerprint": "e" * 64,
                    "role": "migration",
                },
            ],
            "variant_ids": ["main"],
            "active_variant_id": "main",
        },
    }


def test_v2_project_migrates_to_validated_v3_registry_after_verified_backup() -> None:
    filesystem = MemoryFilesystem()
    ref = ProjectRef(ProjectId("project-1"))
    repository = ProjectRepository(ROOT, filesystem)
    raw = json.dumps(_v2_document()).encode()
    filesystem.seed(repository.path_for(ref), raw)

    result = repository.load(ref)

    assert isinstance(result, LoadedRecord) and result.migrated
    assert result.value.envelope.schema_version == 3
    sources = result.value.envelope.data["sources"]
    assert all(item["source_id"] not in {"f" * 64, "legacy:xml"} for item in sources)
    assert len(result.value.envelope.data["source_relations"]) == 1
    assert result.migration_report is not None
    assert filesystem.read_bytes(result.migration_report.backup_path) == raw


def test_saving_v2_project_dto_writes_canonical_v3_registry() -> None:
    filesystem = MemoryFilesystem()
    ref = ProjectRef(ProjectId("project-1"))
    repository = ProjectRepository(ROOT, filesystem)
    document = _v2_document()
    legacy = ProjectDto(
        SchemaEnvelope(
            document["schema_version"],
            ref.kind,
            document["id"],
            document["revision"],
            document["data"],
        )
    )

    result = repository.save(ref, legacy)

    persisted = json.loads(filesystem.read_bytes(repository.path_for(ref)))
    assert result.value.envelope.schema_version == 3
    assert persisted == result.value.envelope.to_dict()
    assert persisted["schema_version"] == 3
    assert len(persisted["data"]["source_relations"]) == 1
    assert {source["legacy"]["role"] for source in persisted["data"]["sources"]} == {"primary", "migration"}


def test_failed_v2_registry_migration_keeps_original_and_verified_backup() -> None:
    filesystem = MemoryFilesystem()
    ref = ProjectRef(ProjectId("project-1"))
    repository = ProjectRepository(ROOT, filesystem)
    raw = json.dumps(_v2_document(format_id="unknown.format")).encode()
    path = repository.path_for(ref)
    filesystem.seed(path, raw)

    result = repository.load(ref)

    assert isinstance(result, QuarantineResult)
    assert result.reason_code == "SOURCE_REGISTRY_MIGRATION_FAILED"
    assert filesystem.read_bytes(path) == raw
    digest = hashlib.sha256(raw).hexdigest()
    assert filesystem.read_bytes(repository._paths.backup(ref, digest, 2)) == raw
