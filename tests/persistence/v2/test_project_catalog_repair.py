from __future__ import annotations

import json
import os

import pytest

from transbridge.persistence.project_catalog import V2ProjectCatalog
from transbridge.persistence.project_catalog_repair import (
    ProjectCatalogRepairService,
    ProjectCatalogRepairStatus,
)
from transbridge.persistence.v2 import ProjectId, ProjectRef, ProjectRepository, VariantRepository
from transbridge.persistence.v2.lifecycle_transactions import ProjectLifecycleTransactionStore
from transbridge.persistence.v2.schema import serialize_document

from .fakes import MemoryFilesystem


def _document(project_id: str, name: str, *, schema_version: int = 2, entity_type: str = "project") -> bytes:
    return serialize_document({
        "schema_version": schema_version,
        "entity_type": entity_type,
        "id": project_id,
        "revision": 0,
        "data": {
            "name": name,
            "sources": [],
            "variant_ids": [],
            "active_variant_id": None,
        },
    })


def _root(name: str) -> str:
    return os.path.abspath(name)


def _service(
    root: str,
    filesystem: MemoryFilesystem,
) -> tuple[ProjectCatalogRepairService, ProjectRepository]:
    repository = ProjectRepository(root, filesystem)
    return ProjectCatalogRepairService(root, filesystem, repository), repository


def _catalog_path(root: str) -> str:
    return os.path.join(root, "project-catalog.json")


def _seed_project(
    filesystem: MemoryFilesystem,
    repository: ProjectRepository,
    project_id: str,
    name: str,
    *,
    schema_version: int = 2,
    entity_type: str = "project",
) -> str:
    path = repository.path_for(ProjectRef(ProjectId(project_id)))
    filesystem.seed(path, _document(project_id, name, schema_version=schema_version, entity_type=entity_type))
    return path


def test_missing_catalog_rebuilds_all_valid_projects_and_is_idempotent() -> None:
    root = _root("repair-all-projects")
    filesystem = MemoryFilesystem()
    service, repository = _service(root, filesystem)
    _seed_project(filesystem, repository, "project-b", " 乙工程 ")
    _seed_project(filesystem, repository, "project-a", "甲工程")

    first = service.repair_if_missing()
    published = json.loads(filesystem.read_bytes(_catalog_path(root)))
    calls_after_first = tuple(filesystem.calls)
    second = service.repair_if_missing()

    assert first.status is ProjectCatalogRepairStatus.REBUILT
    assert first.recovered_count == 2
    assert first.skipped_count == 0
    assert list(published["projects"]) == ["project-a", "project-b"]
    assert published["projects"]["project-a"] == {"name": "甲工程", "name_key": "甲工程"}
    assert published["projects"]["project-b"] == {"name": "乙工程", "name_key": "乙工程"}
    assert second.status is ProjectCatalogRepairStatus.NOT_NEEDED
    assert not any(operation == "list" for operation, _path in filesystem.calls[len(calls_after_first) :])
    assert sum(operation == "replace" for operation, _path in filesystem.calls) == 1


def test_rebuilt_catalog_preserves_active_pointer_and_query_remains_read_only() -> None:
    root = _root("repair-active-project")
    filesystem = MemoryFilesystem()
    service, repository = _service(root, filesystem)
    _seed_project(filesystem, repository, "project-a", "甲工程")
    _seed_project(filesystem, repository, "project-b", "乙工程")
    active_path = os.path.join(root, "active-project.json")
    active_payload = json.dumps(
        {"schema_version": 1, "project_id": "project-b", "variant_id": None},
        ensure_ascii=False,
    ).encode()
    filesystem.seed(active_path, active_payload)

    report = service.repair_if_missing()
    filesystem.calls.clear()
    snapshot = V2ProjectCatalog(root, filesystem, repository).list_projects()

    assert report.status is ProjectCatalogRepairStatus.REBUILT
    assert [project.project_id for project in snapshot.projects] == ["project-b", "project-a"]
    assert snapshot.projects[0].active
    assert filesystem.read_bytes(active_path) == active_payload
    assert not {"write", "replace", "remove", "mkdir"} & {operation for operation, _path in filesystem.calls}


@pytest.mark.parametrize(
    ("payload", "expected_status"),
    [
        (b'{"schema_version":1,"projects":{}}', ProjectCatalogRepairStatus.NOT_NEEDED),
        (b"{broken", ProjectCatalogRepairStatus.BLOCKED),
        (b'{"schema_version":2,"projects":{}}', ProjectCatalogRepairStatus.BLOCKED),
    ],
)
def test_existing_catalog_is_never_scanned_or_rewritten(
    payload: bytes,
    expected_status: ProjectCatalogRepairStatus,
) -> None:
    root = _root(f"repair-existing-{expected_status.value}-{len(payload)}")
    filesystem = MemoryFilesystem()
    service, repository = _service(root, filesystem)
    _seed_project(filesystem, repository, "project-a", "甲工程")
    path = _catalog_path(root)
    filesystem.seed(path, payload)
    filesystem.calls.clear()

    report = service.repair_if_missing()

    assert report.status is expected_status
    assert filesystem.files[path] == payload
    assert not {"list", "write", "replace", "remove", "mkdir"} & {operation for operation, _path in filesystem.calls}


def test_invalid_and_noncanonical_candidates_are_skipped_without_touching_sources() -> None:
    root = _root("repair-mixed-candidates")
    filesystem = MemoryFilesystem()
    service, repository = _service(root, filesystem)
    valid_path = _seed_project(filesystem, repository, "project-valid", "合法工程")
    invalid_path = os.path.join(root, "projects", "random.json")
    invalid_payload = b"not-json"
    filesystem.seed(invalid_path, invalid_payload)
    noncanonical_path = os.path.join(root, "projects", "copied.json")
    noncanonical_payload = _document("project-copy", "复制工程")
    filesystem.seed(noncanonical_path, noncanonical_payload)

    report = service.repair_if_missing()
    published = json.loads(filesystem.read_bytes(_catalog_path(root)))

    assert report.status is ProjectCatalogRepairStatus.REBUILT
    assert report.recovered_count == 1
    assert report.skipped_count == 2
    assert set(published["projects"]) == {"project-valid"}
    assert filesystem.files[valid_path] == _document("project-valid", "合法工程")
    assert filesystem.files[invalid_path] == invalid_payload
    assert filesystem.files[noncanonical_path] == noncanonical_payload
    assert not any(
        operation == "remove" and path in {valid_path, invalid_path, noncanonical_path}
        for operation, path in filesystem.calls
    )


@pytest.mark.parametrize(
    ("schema_version", "entity_type"),
    [(1, "project"), (3, "project"), (2, "session")],
)
def test_legacy_future_and_wrong_entity_records_are_not_repaired(
    schema_version: int,
    entity_type: str,
) -> None:
    root = _root(f"repair-unsupported-{schema_version}-{entity_type}")
    filesystem = MemoryFilesystem()
    service, repository = _service(root, filesystem)
    source_path = _seed_project(
        filesystem,
        repository,
        "project-a",
        "工程 A",
        schema_version=schema_version,
        entity_type=entity_type,
    )
    original = filesystem.files[source_path]

    report = service.repair_if_missing()

    assert report.status is ProjectCatalogRepairStatus.NO_PROJECTS
    assert report.skipped_count == 1
    assert _catalog_path(root) not in filesystem.files
    assert filesystem.files[source_path] == original


def test_casefold_name_conflict_blocks_the_whole_rebuild() -> None:
    root = _root("repair-name-conflict")
    filesystem = MemoryFilesystem()
    service, repository = _service(root, filesystem)
    _seed_project(filesystem, repository, "project-a", "My Mod")
    _seed_project(filesystem, repository, "project-b", "  MY MOD  ")

    report = service.repair_if_missing()

    assert report.status is ProjectCatalogRepairStatus.BLOCKED
    assert report.diagnostics[-1].code == "PROJECT_CATALOG_REPAIR_NAME_CONFLICT"
    assert _catalog_path(root) not in filesystem.files
    assert not any(operation == "replace" for operation, _path in filesystem.calls)


def test_empty_root_does_not_create_an_empty_catalog() -> None:
    root = _root("repair-empty-root")
    filesystem = MemoryFilesystem()
    service, _repository = _service(root, filesystem)

    report = service.repair_if_missing()

    assert report.status is ProjectCatalogRepairStatus.NO_PROJECTS
    assert _catalog_path(root) not in filesystem.files
    assert not any(operation in {"write", "replace", "mkdir"} for operation, _path in filesystem.calls)


def test_discovery_and_publication_faults_leave_catalog_retryable() -> None:
    discovery_root = _root("repair-discovery-fault")
    discovery_filesystem = MemoryFilesystem()
    discovery_service, _repository = _service(discovery_root, discovery_filesystem)
    discovery_filesystem.fail_list_paths.add(os.path.join(discovery_root, "projects"))

    discovery = discovery_service.repair_if_missing()

    assert discovery.status is ProjectCatalogRepairStatus.FAILED
    assert discovery.diagnostics[-1].code == "PROJECT_CATALOG_REPAIR_DISCOVERY_FAILED"
    assert discovery.diagnostics[-1].retryable
    assert _catalog_path(discovery_root) not in discovery_filesystem.files

    publish_root = _root("repair-publish-fault")
    publish_filesystem = MemoryFilesystem()
    publish_service, publish_repository = _service(publish_root, publish_filesystem)
    source_path = _seed_project(publish_filesystem, publish_repository, "project-a", "工程 A")
    original = publish_filesystem.files[source_path]
    publish_filesystem.fail_replace_destinations.add(_catalog_path(publish_root))

    publish = publish_service.repair_if_missing()

    assert publish.status is ProjectCatalogRepairStatus.FAILED
    assert publish.diagnostics[-1].code == "PROJECT_CATALOG_REPAIR_PUBLISH_FAILED"
    assert publish.diagnostics[-1].retryable
    assert _catalog_path(publish_root) not in publish_filesystem.files
    assert publish_filesystem.files[source_path] == original
    assert not any(path.endswith(".tmp") for path in publish_filesystem.files)


def test_alias_that_resolves_outside_the_root_is_skipped() -> None:
    root = _root("repair-alias-escape")
    filesystem = MemoryFilesystem()
    service, _repository = _service(root, filesystem)
    candidate = os.path.join(root, "projects", "escape.json")
    outside = _root("outside-repair-root")
    filesystem.files[candidate] = _document("project-escape", "逃逸工程")
    filesystem.canonical_aliases[candidate] = os.path.join(outside, "escape.json")

    report = service.repair_if_missing()

    assert report.status is ProjectCatalogRepairStatus.NO_PROJECTS
    assert report.diagnostics[0].code == "PROJECT_CATALOG_REPAIR_NONCANONICAL"
    assert _catalog_path(root) not in filesystem.files


def test_projects_directory_alias_outside_root_returns_a_failure_report() -> None:
    root = _root("repair-projects-directory-escape")
    filesystem = MemoryFilesystem()
    repository = ProjectRepository(root, filesystem)
    service = ProjectCatalogRepairService(root, filesystem, repository)
    filesystem.canonical_aliases[os.path.join(root, "projects")] = _root("outside-projects-directory")

    report = service.repair_if_missing()

    assert report.status is ProjectCatalogRepairStatus.FAILED
    assert report.diagnostics[0].code == "PROJECT_CATALOG_REPAIR_PATH_INVALID"
    assert report.diagnostics[0].retryable
    assert _catalog_path(root) not in filesystem.files


def test_lifecycle_refuses_to_rewrite_a_catalog_that_repair_marked_invalid() -> None:
    root = _root("repair-existing-invalid-lifecycle")
    filesystem = MemoryFilesystem()
    service, repository = _service(root, filesystem)
    catalog_path = _catalog_path(root)
    original = '{"schema_version":1,"projects":{"project-a":{"name":"工程 A","name_key":"wrong"}}}'.encode()
    filesystem.seed(catalog_path, original)

    report = service.repair_if_missing()
    lifecycle = ProjectLifecycleTransactionStore(
        root,
        filesystem,
        repository,
        VariantRepository(root, filesystem),
    )

    with pytest.raises(RuntimeError, match="Project catalog is invalid"):
        lifecycle.project_name_exists("工程 a")

    assert report.status is ProjectCatalogRepairStatus.BLOCKED
    assert filesystem.files[catalog_path] == original
