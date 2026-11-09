from __future__ import annotations

from pathlib import Path

from transbridge.application.contracts import RequestContext
from transbridge.application.projects import ProjectCatalogQuery
from transbridge.bootstrap import build_runtime
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.project_store.paths import project_path
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.persistence.v2 import OsPersistenceFilesystem, ProjectId, ProjectRef, ProjectRepository, VariantId
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
    assert project_catalog.list_projects().projects == ()
    assert runtime.close().is_success


def test_bootstrap_discovers_current_project_without_repairing_legacy_catalog(tmp_path: Path) -> None:
    filesystem = OsPersistenceFilesystem()
    repository = ProjectRepository(str(tmp_path), filesystem)
    first = ProjectRef(ProjectId("project-a"))
    second = ProjectRef(ProjectId("project-b"))
    for ref, name in ((first, "蕾米尔"), (second, "艺术馆")):
        path = Path(repository.path_for(ref))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_project_document(ref.identity.value, name))
    pointer = tmp_path / "active-project.json"
    pointer.write_text(
        '{"schema_version":1,"project_id":"project-a","variant_id":null}',
        encoding="utf-8",
    )
    old_paths = (Path(repository.path_for(first)), Path(repository.path_for(second)), pointer)
    original = {path: path.read_bytes() for path in old_paths}
    new_project = ProjectId("current-project")
    ProjectStore(project_path(tmp_path, new_project)).create(new_project, "当前工程", VariantId("main"), "默认")

    services = build_persistence_v2_services(
        tmp_path,
        id_factory=lambda: "id-1",
        timestamp_factory=lambda: "2026-08-25T00:00:00+08:00",
        filesystem=filesystem,
    )
    try:
        (current,) = services.project_catalog.list_projects().projects
        assert current.project_id == new_project.value
        assert not current.active
        prepared = services.current_project_opener.prepare_active(RequestContext("gui"))
        assert not prepared.is_success
        assert prepared.diagnostics[0].code == "ACTIVE_PROJECT_REQUIRED"
    finally:
        services.close()
    assert not (tmp_path / "project-catalog.json").exists()
    assert {path: path.read_bytes() for path in old_paths} == original
