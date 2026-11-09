from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.project_store import lifecycle as storage_lifecycle
from transbridge.persistence.project_store.repository import ProjectStore


def _services(tmp_path):
    return build_persistence_v2_services(
        tmp_path,
        id_factory=lambda: uuid4().hex,
        timestamp_factory=lambda: "2026-09-01T00:00:00+08:00",
    )


def test_authoritative_rename_updates_project_and_catalog_without_changing_identity(tmp_path):
    services = _services(tmp_path)
    context = RequestContext("gui", run_id="rename")
    assert services.gui_project_commands.create_project(ProjectProvisioningRequest("旧名称"), context).is_success
    active = services.project_lifecycle.active
    project_path = Path(services.projects.path_for(active.project_ref))

    result = services.project_management.rename(" 新名称 ", context)

    assert result.is_success, result.diagnostics
    assert services.project_lifecycle.active.project_ref == active.project_ref
    assert services.project_lifecycle.active.project.envelope.revision == active.project.envelope.revision + 1
    assert ProjectStore(project_path).summary().name == "新名称"
    entries = services.project_catalog.list_projects().projects
    assert [(entry.project_id, entry.name) for entry in entries] == [(active.project_ref.identity.value, "新名称")]


def test_rename_write_failure_rolls_back_project_and_catalog(tmp_path, monkeypatch):
    services = _services(tmp_path)
    context = RequestContext("gui", run_id="rename-rollback")
    assert services.gui_project_commands.create_project(ProjectProvisioningRequest("原名称"), context).is_success
    active = services.project_lifecycle.active
    path = Path(services.projects.path_for(active.project_ref))
    before = ProjectStore(path).summary()
    original_write = storage_lifecycle._write_project

    def fail_after_write(*args):
        original_write(*args)
        raise OSError("injected rename write failure")

    monkeypatch.setattr(storage_lifecycle, "_write_project", fail_after_write)
    result = services.project_management.rename("新名称", context)

    assert not result.is_success
    assert services.project_lifecycle.active is active
    assert ProjectStore(path).summary() == before
    assert [project.name for project in services.project_catalog.list_projects().projects] == ["原名称"]


def test_rename_rejects_another_projects_normalized_name(tmp_path):
    services = _services(tmp_path)
    context = RequestContext("gui", run_id="rename-conflict")
    try:
        for name in ("Existing", "Second"):
            assert services.gui_project_commands.create_project(ProjectProvisioningRequest(name), context).is_success
        active = services.project_lifecycle.active

        rejected = services.project_management.rename(" EXISTING ", context)

        assert not rejected.is_success
        assert rejected.diagnostics[0].code == "PROJECT_NAME_ALREADY_EXISTS"
        assert services.project_lifecycle.active is active
        assert [item.name for item in services.project_catalog.list_projects().projects] == ["Existing", "Second"]
    finally:
        services.close()


def test_delete_current_project_removes_owned_aggregate_but_preserves_external_source(tmp_path):
    source = tmp_path.parent / f"external-{uuid4().hex}.xml"
    fixture = Path(__file__).parents[2] / "contracts" / "io" / "fixtures" / "xt-small.xml"
    source.write_bytes(fixture.read_bytes())
    services = _services(tmp_path)
    context = RequestContext("gui", run_id="delete")
    request = ProjectProvisioningRequest("待删除", source=ProjectSourceRequest(str(source), FormatId.XML_XT))
    assert services.gui_project_commands.create_project(request, context).is_success
    active = services.project_lifecycle.active
    assert services.gui_project_commands.save_snapshot("删除测试", context).is_success

    rejected = services.project_management.delete(
        active.project_ref.identity.value,
        context,
        expected_name="已经变化的名称",
    )
    assert not rejected.is_success
    assert Path(services.projects.path_for(active.project_ref)).exists()
    assert source.exists()

    result = services.project_management.delete(
        active.project_ref.identity.value,
        context,
        expected_name="待删除",
    )

    assert result.is_success, result.diagnostics
    assert result.value["external_sources_deleted"] is False
    assert source.exists()
    assert services.project_lifecycle.active is None
    assert not Path(services.projects.path_for(active.project_ref)).exists()
    assert not Path(services.projects.path_for(active.project_ref)).parent.exists()
    assert services.project_catalog.list_projects().projects == ()
    assert not services.current_project_opener.has_active_reference


def test_delete_noncurrent_project_does_not_interrupt_current_project(tmp_path):
    services = _services(tmp_path)
    context = RequestContext("gui", run_id="delete-noncurrent")
    assert services.gui_project_commands.create_project(ProjectProvisioningRequest("工程一"), context).is_success
    first = services.project_lifecycle.active.project_ref
    assert services.gui_project_commands.create_project(ProjectProvisioningRequest("工程二"), context).is_success
    second = services.project_lifecycle.active.project_ref

    result = services.project_management.delete(first.identity.value, context, expected_name="工程一")

    assert result.is_success, result.diagnostics
    assert result.value["active_removed"] is False
    assert services.project_lifecycle.active.project_ref == second
    assert [item.name for item in services.project_catalog.list_projects().projects] == ["工程二"]


def test_delete_failure_restores_owned_files_before_republishing_catalog(tmp_path, monkeypatch):
    services = _services(tmp_path)
    context = RequestContext("gui", run_id="delete-rollback")
    assert services.gui_project_commands.create_project(ProjectProvisioningRequest("可恢复工程"), context).is_success
    active = services.project_lifecycle.active
    project_path = Path(services.projects.path_for(active.project_ref))
    before = project_path.read_bytes()
    original_rename = Path.rename

    def fail_project_rename(path, target):
        if path == project_path.parent:
            raise OSError("injected project isolation failure")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_project_rename)

    result = services.project_management.delete(
        active.project_ref.identity.value,
        context,
        expected_name="可恢复工程",
    )

    assert not result.is_success
    assert project_path.read_bytes() == before
    assert [item.name for item in services.project_catalog.list_projects().projects] == ["可恢复工程"]
    assert services.project_lifecycle.active.project_ref == active.project_ref
