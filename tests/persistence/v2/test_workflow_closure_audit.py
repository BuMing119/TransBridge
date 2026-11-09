"""End-to-end regression coverage for catalog and lifecycle publication gaps."""

from pathlib import Path
from uuid import uuid4

import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.projects import ProjectProvisioningRequest
from transbridge.application.projects.models import TransitionTarget
from transbridge.bootstrap.persistence import build_persistence_v2_services


@pytest.fixture
def service_factory(tmp_path):
    opened = []

    def create():
        services = build_persistence_v2_services(
            tmp_path,
            id_factory=lambda: uuid4().hex,
            timestamp_factory=lambda: "2026-10-07T12:00:00+08:00",
        )
        opened.append(services)
        return services

    try:
        yield create
    finally:
        for services in reversed(opened):
            services.close()


def _new_project(service_factory):
    services = service_factory()
    context = RequestContext("audit", run_id="workflow-closure")
    result = services.gui_project_commands.create_project(ProjectProvisioningRequest("Original"), context)
    assert result.is_success, result.diagnostics
    return services, context


def _open_current(service_factory, context):
    services = service_factory()
    prepared = services.current_project_opener.prepare_active(context)
    assert prepared.is_success, prepared.diagnostics
    activated = services.current_project_opener.activate(prepared.value, context)
    assert activated.is_success, activated.diagnostics
    return services


def test_creating_variant_from_stale_window_preserves_persisted_project_rename(service_factory):
    first, context = _new_project(service_factory)
    second = _open_current(service_factory, context)
    ref = first.project_lifecycle.active.project_ref
    renamed = second.project_management.rename("Renamed by other window", context)
    assert renamed.is_success, renamed.diagnostics

    first.gui_project_commands.create_variant("Branch", context, copy_active=True)

    persisted = first.projects.read_snapshot(ref)
    assert persisted.envelope.data["name"] == "Renamed by other window"
    assert first.project_catalog.list_projects().projects[0].name == persisted.envelope.data["name"]


def test_prepared_variant_switch_preserves_project_update_published_before_commit(service_factory):
    first, context = _new_project(service_factory)
    original = first.project_lifecycle.active.formal_variant_ref
    created = first.gui_project_commands.create_variant("Branch", context)
    assert created.is_success, created.diagnostics
    active = first.project_lifecycle.active
    second = _open_current(service_factory, context)
    prepared = first.project_lifecycle.prepare_transition(TransitionTarget(active.project_ref, original), context)
    assert prepared.is_success, prepared.diagnostics
    renamed = second.project_management.rename("Renamed after preparation", context)
    assert renamed.is_success, renamed.diagnostics

    first.project_lifecycle.commit_transition(prepared.value["token"], context)

    persisted = first.projects.read_snapshot(active.project_ref)
    assert persisted.envelope.data["name"] == "Renamed after preparation"


def test_variant_creation_rechecks_revision_at_publication_after_catalog_preflight(service_factory, monkeypatch):
    first, context = _new_project(service_factory)
    second = _open_current(service_factory, context)
    ref = first.project_lifecycle.active.project_ref
    original_id = first.gui_project_commands._id_factory
    renamed = False

    def allocate_variant_after_other_window_renames():
        nonlocal renamed
        if not renamed:
            renamed = True
            result = second.project_management.rename("Renamed during creation", context)
            assert result.is_success, result.diagnostics
        return original_id()

    monkeypatch.setattr(first.gui_project_commands, "_id_factory", allocate_variant_after_other_window_renames)
    result = first.gui_project_commands.create_variant("Branch", context)
    assert renamed
    assert not result.is_success

    persisted = first.projects.read_snapshot(ref)
    assert persisted.envelope.data["name"] == "Renamed during creation"


def _fail_active_preference(services, monkeypatch):
    original_replace = services.filesystem.replace_durable

    def fail_active_pointer(source, destination):
        if Path(destination) == Path(services.root) / "project-store-v1" / "active.json":
            raise OSError("injected active preference publication failure")
        return original_replace(source, destination)

    monkeypatch.setattr(services.filesystem, "replace_durable", fail_active_pointer)


def test_open_does_not_silently_leave_previous_active_preference(service_factory, monkeypatch):
    services, context = _new_project(service_factory)
    previous = services.project_lifecycle.active
    commands = services.gui_project_commands
    assert commands.create_project(ProjectProvisioningRequest("Other"), context).is_success
    other = services.project_lifecycle.active
    _fail_active_preference(services, monkeypatch)

    opened = services.current_project_opener.open_path(services.projects.path_for(previous.project_ref), context)

    assert opened.is_success
    assert [item.code for item in opened.diagnostics] == ["PROJECT_ACTIVE_PREFERENCE_WRITE_FAILED"]
    assert services.project_lifecycle.active.project_ref == previous.project_ref
    persisted = services.current_project_opener.prepare_active(context)
    assert persisted.is_success, persisted.diagnostics
    assert persisted.value.project_ref == other.project_ref


def test_variant_write_failure_preserves_catalog_and_active_revision(service_factory, monkeypatch):
    from transbridge.persistence.project_store import lifecycle as storage_lifecycle

    services, context = _new_project(service_factory)
    active = services.project_lifecycle.active
    path = Path(services.projects.path_for(active.project_ref))
    before = path.read_bytes()
    original = storage_lifecycle._write_project

    def fail_after_variant_was_staged(*args):
        original(*args)
        raise OSError("injected variant publication failure")

    monkeypatch.setattr(storage_lifecycle, "_write_project", fail_after_variant_was_staged)
    result = services.gui_project_commands.create_variant("Must be rolled back", context)

    assert not result.is_success
    assert services.project_lifecycle.active is active
    assert path.read_bytes() == before
