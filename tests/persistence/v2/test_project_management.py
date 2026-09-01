from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.application.projects.models import LifecycleProjectUpdate
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.project_catalog_document import ProjectCatalogRecord, build_project_catalog
from transbridge.persistence.v2 import (
    SCHEMA_VERSION,
    ProjectDto,
    ProjectId,
    ProjectRef,
    ProjectRepository,
    SchemaEnvelope,
    VariantRepository,
)
from transbridge.persistence.v2.atomic_documents import AtomicDocumentStore
from transbridge.persistence.v2.lifecycle_transactions import ProjectLifecycleTransactionStore

from .fakes import MemoryFilesystem


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
    assert json.loads(project_path.read_text(encoding="utf-8"))["data"]["name"] == "新名称"
    catalog = json.loads((tmp_path / "project-catalog.json").read_text(encoding="utf-8"))
    assert catalog["projects"][active.project_ref.identity.value]["name"] == "新名称"


def test_rename_catalog_publish_failure_restores_exact_project_preimage():
    root = os.path.abspath(os.path.join(os.sep, "transbridge-project-rename-rollback"))
    filesystem = MemoryFilesystem()
    projects = ProjectRepository(root, filesystem)
    variants = VariantRepository(root, filesystem)
    ref = ProjectRef(ProjectId("project-1"))
    current = ProjectDto(
        SchemaEnvelope(
            SCHEMA_VERSION,
            ref.kind,
            ref.identity.value,
            1,
            {"name": "原名称", "sources": [], "variant_ids": [], "active_variant_id": None},
        )
    )
    projects.save(ref, current)
    documents = AtomicDocumentStore(root, filesystem)
    documents.write_json(
        "project-catalog.json",
        build_project_catalog((ProjectCatalogRecord(ref.identity.value, "原名称", "原名称".casefold()),)),
        "seed-catalog",
    )
    before_project = filesystem.read_bytes(projects.path_for(ref))
    catalog_path = documents.path("project-catalog.json")
    before_catalog = filesystem.read_bytes(catalog_path)
    filesystem.fail_durable_replace_destinations.add(catalog_path)
    data = dict(current.envelope.data)
    data["name"] = "新名称"
    updated = ProjectDto(SchemaEnvelope(SCHEMA_VERSION, ref.kind, ref.identity.value, 2, data))
    store = ProjectLifecycleTransactionStore(root, filesystem, projects, variants)
    store.begin("rename-fault")
    store.stage_project_update("rename-fault", LifecycleProjectUpdate(updated, 1))

    with pytest.raises(OSError):
        store.commit("rename-fault")

    assert filesystem.read_bytes(projects.path_for(ref)) == before_project
    assert filesystem.read_bytes(catalog_path) == before_catalog


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
    assert not (tmp_path / "projects" / active.project_ref.identity.encoded).exists()
    assert not tuple((tmp_path / "snapshots").glob("*.json"))
    assert services.project_catalog.list_projects().projects == ()
    active_pointer = json.loads((tmp_path / "active-project.json").read_text(encoding="utf-8"))
    assert active_pointer["project_id"] is None


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
    variant_path = Path(services.variants.path_for(active.formal_variant_ref))
    before_project = project_path.read_bytes()
    before_variant = variant_path.read_bytes()
    original_remove = services.filesystem.remove
    failed = False

    def fail_project_remove(path, *, missing_ok=False):
        nonlocal failed
        if not failed and services.filesystem.canonicalize(path) == services.filesystem.canonicalize(str(project_path)):
            failed = True
            raise OSError("injected Project delete fault")
        return original_remove(path, missing_ok=missing_ok)

    monkeypatch.setattr(services.filesystem, "remove", fail_project_remove)

    result = services.project_management.delete(
        active.project_ref.identity.value,
        context,
        expected_name="可恢复工程",
    )

    assert not result.is_success
    assert project_path.read_bytes() == before_project
    assert variant_path.read_bytes() == before_variant
    assert [item.name for item in services.project_catalog.list_projects().projects] == ["可恢复工程"]
    assert services.project_lifecycle.active.project_ref == active.project_ref
