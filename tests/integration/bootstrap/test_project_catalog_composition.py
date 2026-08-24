from __future__ import annotations

from pathlib import Path

from transbridge.application.projects import ProjectCatalogQuery
from transbridge.bootstrap import build_runtime
from transbridge.bootstrap.persistence import build_persistence_v2_services


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
    assert project_catalog.list_projects().projects == ()
    assert runtime.close().is_success
