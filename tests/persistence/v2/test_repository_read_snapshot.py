"""Pure repository reads validate and migrate in memory without changing the data tree."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from transbridge.persistence.v2 import (
    SCHEMA_VERSION,
    ProjectDto,
    ProjectId,
    ProjectRef,
    ProjectRepository,
    SchemaValidationError,
    SessionDto,
    SessionId,
    SessionRef,
    SessionRepository,
    VariantDto,
    VariantId,
    VariantRef,
    VariantRepository,
)
from transbridge.persistence.v2.migration import migrate_to_current, migrate_v1
from transbridge.persistence.v2.schema import validate_v2

from .fakes import MemoryFilesystem

ROOT = os.path.abspath(os.path.join(os.sep, "transbridge-snapshot"))
RECORDS = (
    (ProjectRepository, ProjectRef(ProjectId("project-1")), ProjectDto, "project-v1.json"),
    (VariantRepository, VariantRef(VariantId("main"), ProjectId("project-1")), VariantDto, "variant-v1.json"),
    (SessionRepository, SessionRef(SessionId("session-1")), SessionDto, "session-v1.json"),
)


@pytest.fixture(params=RECORDS, ids=("project", "variant", "session"))
def record(request):
    repository, ref, dto_type, fixture_name = request.param
    filesystem = MemoryFilesystem()
    repo = repository(ROOT, filesystem)
    document = json.loads((Path(__file__).with_name("fixtures") / fixture_name).read_bytes())
    return filesystem, repo, ref, dto_type, document


@pytest.mark.parametrize("schema", [1, 2, SCHEMA_VERSION])
def test_read_snapshot_returns_validated_current_dto_without_writes(record, schema):
    filesystem, repo, ref, dto_type, original = record
    expected = validate_v2(migrate_to_current(original, ref).document, ref)
    document = original
    if schema == 2:
        document = migrate_v1(original, ref).document
    elif schema == SCHEMA_VERSION:
        document = expected.envelope.to_dict()
    filesystem.seed(repo.path_for(ref), json.dumps(document).encode("utf-8"))
    persisted = dict(filesystem.files), set(filesystem.directories)

    snapshot = repo.read_snapshot(ref)

    assert isinstance(snapshot, dto_type)
    assert snapshot == expected
    assert snapshot.envelope.schema_version == SCHEMA_VERSION
    assert (filesystem.files, filesystem.directories) == persisted


@pytest.mark.parametrize("damage", ["json", "legacy", "schema", "identity", "future"])
def test_read_snapshot_rejects_damaged_or_future_records_without_writes(record, damage):
    filesystem, repo, ref, _dto_type, original = record
    document = migrate_to_current(original, ref).document
    if damage == "schema":
        document["data"] = None
    elif damage == "identity":
        document["id"] = "another-record"
    elif damage == "future":
        document["schema_version"] = SCHEMA_VERSION + 1
    raw = b"{" if damage == "json" else b"{}" if damage == "legacy" else json.dumps(document).encode("utf-8")
    filesystem.seed(repo.path_for(ref), raw)
    persisted = dict(filesystem.files), set(filesystem.directories)

    with pytest.raises(SchemaValidationError) as error:
        repo.read_snapshot(ref)

    assert error.value.code
    assert (filesystem.files, filesystem.directories) == persisted
