from __future__ import annotations

import json
import os

from transbridge.persistence.project_catalog import V2ProjectCatalog
from transbridge.persistence.v2 import ProjectId, ProjectRef, ProjectRepository
from transbridge.persistence.v2.schema import serialize_document

from .fakes import MemoryFilesystem


def _document(project_id: str, name: str) -> bytes:
    return serialize_document({
        "schema_version": 2,
        "entity_type": "project",
        "id": project_id,
        "revision": 0,
        "data": {
            "name": name,
            "sources": [],
            "variant_ids": [],
            "active_variant_id": None,
        },
    })


def _seed_json(filesystem: MemoryFilesystem, path: str, value: object) -> None:
    filesystem.seed(path, json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _catalog(root: str, filesystem: MemoryFilesystem) -> tuple[V2ProjectCatalog, ProjectRepository]:
    repository = ProjectRepository(root, filesystem)
    return V2ProjectCatalog(root, filesystem, repository), repository


def test_catalog_lists_active_first_with_repository_derived_paths() -> None:
    root = os.path.abspath("catalog-root")
    filesystem = MemoryFilesystem()
    catalog, repository = _catalog(root, filesystem)
    first = ProjectRef(ProjectId("project-a"))
    second = ProjectRef(ProjectId("project-b"))
    filesystem.seed(repository.path_for(first), _document("project-a", "甲工程"))
    filesystem.seed(repository.path_for(second), _document("project-b", "乙工程"))
    _seed_json(
        filesystem,
        os.path.join(root, "project-catalog.json"),
        {
            "schema_version": 1,
            "projects": {
                "project-a": {"name": "甲工程", "name_key": "甲工程"},
                "project-b": {"name": "乙工程", "name_key": "乙工程"},
            },
        },
    )
    _seed_json(
        filesystem,
        os.path.join(root, "active-project.json"),
        {"schema_version": 1, "project_id": "project-b", "variant_id": None},
    )
    filesystem.calls.clear()

    snapshot = catalog.list_projects()

    assert [item.project_id for item in snapshot.projects] == ["project-b", "project-a"]
    assert snapshot.projects[0].active is True
    assert all(item.available and item.reason is None for item in snapshot.projects)
    assert snapshot.projects[1].path == repository.path_for(first)
    assert not {"write", "replace", "remove", "mkdir"} & {operation for operation, _path in filesystem.calls}


def test_missing_catalog_recovers_valid_active_project_without_writes() -> None:
    root = os.path.abspath("missing-catalog-root")
    filesystem = MemoryFilesystem()
    catalog, repository = _catalog(root, filesystem)
    active = ProjectRef(ProjectId("project-active"))
    filesystem.seed(repository.path_for(active), _document("project-active", "现有工程"))
    _seed_json(
        filesystem,
        os.path.join(root, "active-project.json"),
        {"schema_version": 1, "project_id": "project-active", "variant_id": None},
    )
    filesystem.calls.clear()

    snapshot = catalog.list_projects()

    assert len(snapshot.projects) == 1
    assert snapshot.projects[0].project_id == "project-active"
    assert snapshot.projects[0].name == "现有工程"
    assert snapshot.projects[0].active and snapshot.projects[0].available
    assert snapshot.projects[0].path == repository.path_for(active)
    assert snapshot.diagnostics[0].code == "ACTIVE_PROJECT_NOT_IN_CATALOG"
    assert not {"write", "replace", "remove", "mkdir"} & {operation for operation, _path in filesystem.calls}


def test_missing_and_corrupt_project_records_remain_visible_but_unavailable() -> None:
    root = os.path.abspath("unavailable-root")
    filesystem = MemoryFilesystem()
    catalog, repository = _catalog(root, filesystem)
    corrupt = ProjectRef(ProjectId("corrupt"))
    filesystem.seed(repository.path_for(corrupt), b"not-json")
    _seed_json(
        filesystem,
        os.path.join(root, "project-catalog.json"),
        {
            "schema_version": 1,
            "projects": {
                "missing": {"name": "缺失工程"},
                "corrupt": {"name": "损坏工程"},
            },
        },
    )

    snapshot = catalog.list_projects()
    by_id = {item.project_id: item for item in snapshot.projects}

    assert by_id["missing"].available is False
    assert by_id["missing"].reason == "工程记录不存在。"
    assert by_id["corrupt"].available is False
    assert by_id["corrupt"].reason == "工程记录已损坏。"


def test_corrupt_catalog_returns_safe_empty_diagnostic_without_rewriting() -> None:
    root = os.path.abspath("corrupt-catalog-root")
    filesystem = MemoryFilesystem()
    catalog, _repository = _catalog(root, filesystem)
    path = os.path.join(root, "project-catalog.json")
    original = b'{"schema_version":1,"projects":{"../escape":{"name":"bad"}}}'
    filesystem.seed(path, original)
    filesystem.calls.clear()

    snapshot = catalog.list_projects()

    assert snapshot.projects == ()
    assert snapshot.diagnostics[0].code == "PROJECT_CATALOG_INVALID"
    assert filesystem.files[path] == original
    assert root not in str(snapshot.to_dict())
    assert not {"write", "replace", "remove", "mkdir"} & {operation for operation, _path in filesystem.calls}


def test_corrupt_active_pointer_does_not_hide_valid_projects() -> None:
    root = os.path.abspath("corrupt-pointer-root")
    filesystem = MemoryFilesystem()
    catalog, repository = _catalog(root, filesystem)
    ref = ProjectRef(ProjectId("project-a"))
    filesystem.seed(repository.path_for(ref), _document("project-a", "工程 A"))
    _seed_json(
        filesystem,
        os.path.join(root, "project-catalog.json"),
        {"schema_version": 1, "projects": {"project-a": {"name": "工程 A"}}},
    )
    filesystem.seed(os.path.join(root, "active-project.json"), b"{broken")

    snapshot = catalog.list_projects()

    assert len(snapshot.projects) == 1
    assert snapshot.projects[0].available is True
    assert snapshot.projects[0].active is False
    assert snapshot.diagnostics[0].code == "ACTIVE_PROJECT_POINTER_INVALID"
