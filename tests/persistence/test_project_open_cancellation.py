"""An explicit cancelled open must not publish recovery over dirty active state."""

from pathlib import Path

from tests.persistence import test_project_recovery as recovery_support
from transbridge.application.contracts import OperationOutcome
from transbridge.application.projects import DirtyDecision
from transbridge.persistence.v2 import VariantSnapshot

project = recovery_support.project


def test_cancelled_open_does_not_prepare_repair_and_dirty_active_can_still_save(project):
    services, context, _source, _raw, path, key = project
    active = services.project_lifecycle.active
    variant_path = Path(services.variants.path_for(active.formal_variant_ref))
    recovery_support._set_schema(path, 2)
    recovery_support._set_schema(variant_path, 2)
    assert services.gui_project_commands.update_entry(key, context, translation="Keep my pending edit").is_success
    project_before, variant_before = Path(path).read_bytes(), variant_path.read_bytes()

    result = services.current_project_opener.open_path(path, context, dirty_decision=DirtyDecision.CANCEL)

    assert result.outcome is OperationOutcome.CANCELLED
    assert services.project_lifecycle.active is active
    assert active.dirty
    assert Path(path).read_bytes() == project_before
    assert variant_path.read_bytes() == variant_before
    saved = services.gui_project_commands.save(context)
    assert saved.is_success, saved.diagnostics
    persisted = VariantSnapshot.from_dto(services.variants.read_snapshot(active.formal_variant_ref))
    assert next(row for row in persisted.entries if row.entry_key == key).translation == "Keep my pending edit"
