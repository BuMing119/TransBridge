"""Fault and contention tests for authoritative SQLite project transitions."""

from pathlib import Path
import subprocess
import sys

from transbridge.persistence.project_store.connection import project_connection
from transbridge.persistence.project_store.repository import ProjectStore

from .test_workflow_closure_audit import _new_project, _open_current, service_factory as service_factory_fixture

service_factory = service_factory_fixture


def test_interrupted_variant_creation_is_rolled_back_on_reopen(service_factory):
    services, context = _new_project(service_factory)
    active = services.project_lifecycle.active
    path = Path(services.projects.path_for(active.project_ref))
    before = ProjectStore(path).summary()
    child = """
import os
import sys
from uuid import uuid4
from transbridge.application.contracts import RequestContext
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.project_store import lifecycle

services = build_persistence_v2_services(sys.argv[1], id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now")
context = RequestContext("audit", run_id="workflow-closure")
opened = services.current_project_opener.open_path(sys.argv[2], context)
assert opened.is_success, opened.diagnostics
original = lifecycle._write_project
def terminate_before_commit(*args):
    original(*args)
    os._exit(19)
lifecycle._write_project = terminate_before_commit
services.gui_project_commands.create_variant("Interrupted", context)
sys.exit(5)
"""
    process = subprocess.run([sys.executable, "-c", child, services.root, str(path)], capture_output=True, timeout=15)

    assert process.returncode == 19, process.stderr.decode(errors="replace")
    reopened = _open_current(service_factory, context)
    assert reopened.project_lifecycle.active.formal_variant_ref == active.formal_variant_ref
    assert ProjectStore(path).summary() == before
    assert reopened.gui_project_commands.create_variant("Retry", context).is_success


def test_second_process_cannot_acquire_sqlite_writer_while_transaction_is_held(service_factory):
    services, _context = _new_project(service_factory)
    active = services.project_lifecycle.active
    path = Path(services.projects.path_for(active.project_ref))
    before = ProjectStore(path).summary()
    child = """
from pathlib import Path
import sys
from transbridge.persistence.project_store.connection import project_connection
from transbridge.persistence.v2.models import ReadOnlyWriteRefused

try:
    with project_connection(Path(sys.argv[1]), write=True):
        sys.exit(5)
except ReadOnlyWriteRefused as exc:
    assert exc.code == "PROJECT_WRITER_BUSY", exc.code
    sys.exit(19)
"""
    with project_connection(path, write=True):
        process = subprocess.run([sys.executable, "-c", child, str(path)], capture_output=True, timeout=15)

    assert process.returncode == 19, process.stderr.decode(errors="replace")
    assert ProjectStore(path).summary() == before


def test_import_publication_fences_create_and_rename_in_another_process(service_factory, tmp_path, monkeypatch):
    services, context = _new_project(service_factory)
    bundle = tmp_path.parent / f"{tmp_path.name}-reserved.transbridge"
    services.project_archive.export_project(str(bundle), context)
    child = """
import sys
from uuid import uuid4
from transbridge.application.contracts import RequestContext
from transbridge.application.projects import ProjectProvisioningRequest
from transbridge.bootstrap.persistence import build_persistence_v2_services

services = build_persistence_v2_services(sys.argv[1], id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now")
context = RequestContext("audit", run_id="workflow-closure")
try:
    prepared = services.current_project_opener.prepare_active(context)
    assert prepared.is_success, prepared.diagnostics
    assert services.current_project_opener.activate(prepared.value, context).is_success
    for result in (
        services.gui_project_commands.create_project(ProjectProvisioningRequest("Reserved"), context),
        services.project_management.rename("Reserved", context),
    ):
        assert not result.is_success, result
        assert result.diagnostics[0].code == "PROJECT_CATALOG_WRITER_BUSY", result.diagnostics
        assert result.diagnostics[0].retryable
finally:
    services.close()
"""
    rename = Path.rename

    def compete_during_publication(path, destination):
        if path.name.startswith(".package-stage-"):
            process = subprocess.run([sys.executable, "-c", child, services.root], capture_output=True, timeout=15)
            assert process.returncode == 0, process.stderr.decode(errors="replace")
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", compete_during_publication)
    imported = services.project_archive.import_project(
        str(bundle), context, requested_name="Reserved", copy_on_identity_conflict=True
    )

    assert ProjectStore(imported).summary().name == "Reserved"
    assert [item.name for item in services.project_catalog.list_projects().projects] == ["Original", "Reserved"]
    assert not tuple(Path(services.root).rglob(".project-stage-*"))
    assert not tuple(Path(services.root).rglob(".package-stage-*"))
    assert not services.project_management.rename("Reserved", context).is_success


def test_deleting_inactive_variant_preserves_retained_variant_and_startup_selection(service_factory):
    services, context = _new_project(service_factory)
    original = services.project_lifecycle.active.formal_variant_ref
    commands = services.gui_project_commands
    assert commands.create_variant("Keep", context, copy_active=True).is_success
    active = services.project_lifecycle.active
    kept = services.variants.read_snapshot(active.formal_variant_ref)
    preference = Path(services.root) / "project-store-v1" / "active.json"
    before = preference.read_bytes()

    deleted = commands.delete_variant(original.identity.value, context)

    assert deleted.is_success, deleted.diagnostics
    assert services.variants.read_snapshot(active.formal_variant_ref) == kept
    assert services.current_project_opener.prepare_active(context).value.variant_ref == active.formal_variant_ref
    assert preference.read_bytes() == before
    assert original.identity not in {
        item.identity for item in ProjectStore(Path(services.projects.path_for(active.project_ref))).summary().variants
    }
