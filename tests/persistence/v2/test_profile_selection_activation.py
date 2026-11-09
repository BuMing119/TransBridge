"""SQLite keeps terminology association and variant text in one transaction."""

from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.application.projects.test_profile_variant_commands import (
    _save,
    _translations,
    scheme_project as scheme_project_fixture,
)
from transbridge.persistence.project_store import lifecycle as storage_lifecycle

from .test_workflow_closure_audit import _open_current, service_factory as service_factory_fixture

scheme_project = scheme_project_fixture
service_factory = service_factory_fixture


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("failure_point", ["before_writes", "before_commit"])
def test_scheme_save_io_failure_rolls_back_both_variant_and_selection(
    scheme_project, service_factory, monkeypatch, overwrite, failure_point
):
    services, context, participant, (_source, target_profile), _keys = scheme_project
    original = services.project_lifecycle.active.formal_variant_ref
    options = {}
    if overwrite:
        assert _save(scheme_project).is_success
        target = services.project_lifecycle.active
        options = {
            "target_variant_id": target.formal_variant_ref.identity.value,
            "expected_target_revision": target.variant.revision,
        }
        assert services.gui_project_commands.switch_v2(target.project_ref, original, context).is_success
    active = services.project_lifecycle.active
    project_path = Path(services.projects.path_for(active.project_ref))
    before = project_path.read_bytes()
    pointer = Path(services.root) / "project-store-v1" / "active.json"
    preference = pointer.read_bytes()
    connection = storage_lifecycle.ProjectLifecycleStore._connection

    @contextmanager
    def faulty_connection(store, project):
        with connection(store, project) as transaction:
            if failure_point == "before_writes":
                raise OSError("injected before project writes")
            yield transaction
            raise OSError("injected after variant and selection writes")

    with monkeypatch.context() as patch:
        patch.setattr(storage_lifecycle.ProjectLifecycleStore, "_connection", faulty_connection)
        failed = _save(scheme_project, **options)

    assert not failed.is_success
    assert services.project_lifecycle.active is active
    assert project_path.read_bytes() == before
    assert pointer.read_bytes() == preference
    assert _open_current(service_factory, context).project_lifecycle.active.formal_variant_ref == original

    committed = _save(scheme_project, **options)
    assert committed.is_success, committed.diagnostics
    reopened = _open_current(service_factory, context)
    variant = reopened.project_lifecycle.active.formal_variant_ref
    assert variant != original
    selected = participant.selection(variant.project_id.value, variant.identity.value)
    assert (selected.profile_id, selected.revision) == (target_profile.profile_id, target_profile.revision)
    assert "白漫和白漫欢迎龙裔。" in _translations(reopened).values()
