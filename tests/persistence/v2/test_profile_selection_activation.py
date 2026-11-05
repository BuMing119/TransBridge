"""Fault injection proves variant text and actual scheme association recover together."""

from dataclasses import replace

import pytest

from tests.application.projects.test_profile_variant_commands import (
    _save,
    _translations,
    scheme_project as scheme_project_fixture,
)
from transbridge.application.contracts import DomainError
from transbridge.persistence.v2.terminology_selection_participant import TerminologySelectionParticipant

from .test_project_activation_transactions import ProcessInterrupted, _formal_files, _intercept
from .test_workflow_closure_audit import _open_current, service_factory as service_factory_fixture

scheme_project = scheme_project_fixture
service_factory = service_factory_fixture


def _selection_rows(participant, project_id):
    with participant.repository(project_id) as repository:
        return tuple(
            tuple(row)
            for row in repository._connection.execute(
                "SELECT * FROM terminology_profile_selections ORDER BY variant_id"
            )
        )


def _action(scheme_project, overwrite):
    services, context, _participant, _profiles, _keys = scheme_project
    original = services.project_lifecycle.active
    options = {}
    if overwrite:
        assert _save(scheme_project).is_success
        target = services.project_lifecycle.active
        options = {
            "target_variant_id": target.formal_variant_ref.identity.value,
            "expected_target_revision": target.variant.revision,
        }
        assert services.gui_project_commands.switch_v2(
            original.project_ref, original.formal_variant_ref, context
        ).is_success
    return lambda: _save(scheme_project, **options)


def _selection_fault(monkeypatch, error, *, after):
    original = TerminologySelectionParticipant.replace
    fired = []

    def fail(participant, project_id, variant_id, payload, *, known):
        if payload is not None and not fired:
            fired.append((project_id, variant_id))
            if after:
                original(participant, project_id, variant_id, payload, known=known)
            raise error("injected SQLite selection publication failure")
        return original(participant, project_id, variant_id, payload, known=known)

    monkeypatch.setattr(TerminologySelectionParticipant, "replace", fail)
    return fired


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("stage", ["prepared", "variant", "selection", "pointer", "committed"])
def test_interrupted_scheme_save_recovers_exact_old_or_committed_pair(
    scheme_project, service_factory, tmp_path, monkeypatch, overwrite, stage
):
    services, context, participant, (_source, target), keys = scheme_project
    action = _action(scheme_project, overwrite)
    original = services.project_lifecycle.active
    project_id = original.project_ref.identity.value
    before = (_formal_files(tmp_path), _selection_rows(participant, project_id))
    with monkeypatch.context() as patch:
        fired = (
            _selection_fault(patch, ProcessInterrupted, after=True)
            if stage == "selection"
            else _intercept(services.filesystem, patch, stage, ProcessInterrupted)
        )
        with pytest.raises(ProcessInterrupted):
            action()
        assert fired
    after_crash = (_formal_files(tmp_path), _selection_rows(participant, project_id))
    expected = after_crash if stage == "committed" else before
    for _ in range(2):
        reopened = _open_current(service_factory, context)
        actual_files = _formal_files(tmp_path)
        assert actual_files.keys() == expected[0].keys()
        for path, payload in expected[0].items():
            assert actual_files[path] == payload, path
        assert _selection_rows(participant, project_id) == expected[1]
        active = reopened.project_lifecycle.active
        if stage == "committed":
            assert active.formal_variant_ref != original.formal_variant_ref
            selected = participant.selection(project_id, active.formal_variant_ref.identity.value)
            assert (selected.profile_id, selected.revision) == (target.profile_id, target.revision)
            assert _translations(reopened)[keys[0]] == "白漫和白漫欢迎龙裔。"
        else:
            assert active.formal_variant_ref == original.formal_variant_ref
        assert not tuple((tmp_path / "project-activation-journal").glob("*.json"))


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("stage", ["variant", "selection", "pointer", "committed"])
@pytest.mark.parametrize("after", [False, True])
def test_io_failure_restores_both_participants_until_commit_is_durable(
    scheme_project, tmp_path, monkeypatch, overwrite, stage, after
):
    services, _context, participant, _profiles, _keys = scheme_project
    action = _action(scheme_project, overwrite)
    original = services.project_lifecycle.active
    project_id = original.project_ref.identity.value
    before = (_formal_files(tmp_path), _selection_rows(participant, project_id))
    with monkeypatch.context() as patch:
        fired = (
            _selection_fault(patch, OSError, after=after)
            if stage == "selection"
            else _intercept(services.filesystem, patch, stage, OSError, after=after)
        )
        result = action()
    assert fired
    if stage == "committed" and after:
        assert result.is_success, result.diagnostics
        assert services.project_lifecycle.active.formal_variant_ref != original.formal_variant_ref
    else:
        assert not result.is_success
        assert services.project_lifecycle.active is original
        assert (_formal_files(tmp_path), _selection_rows(participant, project_id)) == before
    assert not tuple((tmp_path / "project-activation-journal").glob("*.json"))


def test_external_selection_change_fences_recovery_without_overwriting_newer_row(
    scheme_project, service_factory, tmp_path, monkeypatch
):
    services, _context, participant, _profiles, _keys = scheme_project
    with monkeypatch.context() as patch:
        fired = _selection_fault(patch, ProcessInterrupted, after=True)
        with pytest.raises(ProcessInterrupted):
            _save(scheme_project)
    project_id, variant_id = fired[0]
    selected = participant.selection(project_id, variant_id)
    external = replace(selected, selected_at="2026-11-05T23:59:59+00:00")
    with participant.repository(project_id) as repository:
        repository.set_selection(external)
    before = _formal_files(tmp_path)
    with pytest.raises(DomainError, match="newer content"):
        service_factory()
    assert participant.selection(project_id, variant_id) == external
    assert _formal_files(tmp_path) == before
    assert tuple((tmp_path / "project-activation-journal").glob("*.json"))
