"""Cancelling an open leaves the current SQLite working copy saveable."""

from pathlib import Path

from tests.persistence.v2.test_project_archive import (
    exported_project as exported_fixture,
    services_factory as services_fixture,
)
from transbridge.application.contracts import OperationOutcome
from transbridge.application.projects import DirtyDecision, ProjectProvisioningRequest
from transbridge.persistence.v2.variant import VariantSnapshot

exported_project = exported_fixture
services_factory = services_fixture


def test_cancelled_open_does_not_prepare_target_and_dirty_active_can_still_save(exported_project, monkeypatch):
    project = exported_project
    services = project.services
    commands = services.gui_project_commands
    target_path = services.projects.path_for(project.project_ref)
    assert commands.create_project(ProjectProvisioningRequest("Other"), project.context).is_success
    other_path = services.projects.path_for(services.project_lifecycle.active.project_ref)
    assert services.current_project_opener.open_path(target_path, project.context).is_success
    assert commands.update_entry(project.key, project.context, translation="Unsaved change").is_success
    active = services.project_lifecycle.active
    pointer = Path(services.root) / "project-store-v1" / "active.json"
    before = pointer.read_bytes()

    def unexpected_prepare(*_args):
        raise AssertionError("cancelled open prepared the target")

    monkeypatch.setattr(services.current_project_opener, "prepare_path", unexpected_prepare)
    cancelled = services.current_project_opener.open_path(
        other_path, project.context, dirty_decision=DirtyDecision.CANCEL
    )

    assert cancelled.outcome is OperationOutcome.CANCELLED
    assert services.project_lifecycle.active is active
    assert active.dirty and pointer.read_bytes() == before
    assert commands.save(project.context).is_success
    assert not services.project_lifecycle.active.dirty
    ref = active.formal_variant_ref
    snapshot = VariantSnapshot.from_dto(services.variants.read_snapshot(ref), ref)
    assert next(entry for entry in snapshot.entries if entry.entry_key == project.key).translation == "Unsaved change"
