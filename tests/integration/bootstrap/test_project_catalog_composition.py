from __future__ import annotations

from pathlib import Path

from transbridge.application.projects import ProjectCatalogQuery
from transbridge.bootstrap import build_runtime
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.project_catalog_repair import (
    ProjectCatalogRepairStatus,
)
from transbridge.persistence.v2 import OsPersistenceFilesystem, ProjectId, ProjectRef, ProjectRepository
from transbridge.persistence.v2.schema import serialize_document


def _project_document(project_id: str, name: str) -> bytes:
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


def test_persistence_services_expose_read_only_project_catalog(tmp_path: Path) -> None:
    services = build_persistence_v2_services(
        tmp_path,
        id_factory=lambda: "id-1",
        timestamp_factory=lambda: "2026-08-24T00:00:00+08:00",
    )

    assert isinstance(services.project_catalog, ProjectCatalogQuery)
    assert services.project_catalog.list_projects().projects == ()
    services.close()


def test_runtime_registers_project_catalog_use_case(tmp_path: Path) -> None:
    runtime = build_runtime({"persistence_v2_root": tmp_path})

    project_catalog = runtime.use_cases.resolve("project_catalog")

    assert isinstance(project_catalog, ProjectCatalogQuery)
    assert project_catalog is runtime.use_cases.resolve("persistence_v2").project_catalog
    assert "project_catalog_repair" not in runtime.use_cases.names()
    assert runtime.use_cases.resolve("project_catalog_repair_report").status is ProjectCatalogRepairStatus.NO_PROJECTS
    assert project_catalog.list_projects().projects == ()
    assert runtime.close().is_success


def test_bootstrap_rebuilds_a_missing_catalog_before_the_first_query(tmp_path: Path) -> None:
    filesystem = OsPersistenceFilesystem()
    repository = ProjectRepository(str(tmp_path), filesystem)
    first = ProjectRef(ProjectId("project-a"))
    second = ProjectRef(ProjectId("project-b"))
    for ref, name in ((first, "蕾米尔"), (second, "艺术馆")):
        path = Path(repository.path_for(ref))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_project_document(ref.identity.value, name))
    (tmp_path / "active-project.json").write_text(
        '{"schema_version":1,"project_id":"project-a","variant_id":null}',
        encoding="utf-8",
    )

    services = build_persistence_v2_services(
        tmp_path,
        id_factory=lambda: "id-1",
        timestamp_factory=lambda: "2026-08-25T00:00:00+08:00",
        filesystem=filesystem,
    )
    snapshot = services.project_catalog.list_projects()

    assert services.project_catalog_repair_report.status is ProjectCatalogRepairStatus.REBUILT
    assert services.project_catalog_repair_report.recovered_count == 2
    assert [project.project_id for project in snapshot.projects] == ["project-a", "project-b"]
    assert snapshot.projects[0].active
    assert (tmp_path / "project-catalog.json").exists()
    services.close()
