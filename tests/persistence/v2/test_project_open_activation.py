"""Real opening transactions prepare views before publication and roll back baselines."""

from dataclasses import dataclass
from itertools import count
import json
from pathlib import Path

import pytest

from transbridge.application.contracts import DomainError, ErrorCategory, RequestContext
from transbridge.application.io import FormatId
from transbridge.application.projects import DirtyDecision, ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.application.projects.transition_preparation import active_signature
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.v2.schema import serialize_document


@dataclass
class Opening:
    services: object
    context: RequestContext
    path: str
    old: object

    def prepare(self):
        prepared = self.services.current_project_opener.prepare_path(self.path, self.context)
        assert prepared.is_success, prepared.diagnostics
        assert prepared.value.recovery is None
        return prepared.value

    def persisted(self):
        root = Path(self.services.root)
        paths = [*root.glob("projects/**/*.json"), root / "active-project.json", root / "project-catalog.json"]
        return {str(path.relative_to(root)): path.read_bytes() for path in paths if path.exists()}


@pytest.fixture
def opening(tmp_path):
    source = tmp_path / "source.xml"
    source.write_bytes(Path("tests/contracts/io/fixtures/eet-small.xml").read_bytes())
    identities = count()
    services = build_persistence_v2_services(
        tmp_path / "data",
        id_factory=lambda: str(next(identities)),
        timestamp_factory=lambda: "2026-10-19T00:00:00+00:00",
    )
    context = RequestContext("opening-test", run_id="opening")
    request_source = ProjectSourceRequest(str(source), FormatId.XML_EET)
    try:
        result = services.gui_project_commands.create_project(
            ProjectProvisioningRequest("Target", source=request_source), context
        )
        assert result.is_success, result.diagnostics
        assert services.gui_project_commands.create_variant("Branch", context, copy_active=True).is_success
        target = services.project_lifecycle.active
        path = services.projects.path_for(target.project_ref)
        result = services.gui_project_commands.create_project(
            ProjectProvisioningRequest("Old", source=request_source), context
        )
        assert result.is_success, result.diagnostics
        yield Opening(services, context, path, services.project_lifecycle.active)
    finally:
        services.close()


def _catalog(opening, prepared):
    return opening.services.baselines.capture_catalog(prepared.project_ref, prepared.variant_refs)


def _assert_catalog_identity(current, previous):
    assert tuple(ref for ref, _baselines in current) == tuple(ref for ref, _baselines in previous)
    assert all(
        current_item[1] is previous_item[1] for current_item, previous_item in zip(current, previous, strict=True)
    )


@pytest.mark.parametrize("remove_registration", [False, True])
def test_failed_view_preparation_restores_active_pointer_projection_and_all_baselines(opening, remove_registration):
    services = opening.services
    prepared = opening.prepare()
    if remove_registration:
        services.baselines.remove(prepared.project_ref, prepared.variant_refs[0])
    previous_catalog = _catalog(opening, prepared)
    persisted = opening.persisted()
    projection = services.project_projection.snapshot()
    seen = []

    def prepare_view(snapshot):
        assert services.project_lifecycle.active is opening.old
        assert opening.persisted() == persisted
        assert snapshot.values["project_name"] == "Target"
        assert all(baselines is prepared.baselines for _ref, baselines in _catalog(opening, prepared))
        seen.append(snapshot)
        raise DomainError(ErrorCategory.INPUT, "PROJECT_VIEW_FAILED", "Cannot prepare detached project view.")

    result = services.current_project_opener.activate(prepared, opening.context, prepare_view=prepare_view)

    assert result.diagnostics[0].code == "PROJECT_VIEW_FAILED"
    assert len(seen) == 1
    assert services.project_lifecycle.active is opening.old
    assert services.project_projection.snapshot() is projection
    assert opening.persisted() == persisted
    _assert_catalog_identity(_catalog(opening, prepared), previous_catalog)


def test_view_payload_is_completed_before_publication_and_returned_after_commit(opening):
    services = opening.services
    prepared = opening.prepare()
    pointer = Path(services.root) / "active-project.json"
    before_pointer = pointer.read_bytes()
    payload = {"prepared": True}

    def prepare_view(snapshot):
        assert services.project_lifecycle.active is opening.old
        assert pointer.read_bytes() == before_pointer
        assert snapshot.values["project_id"] == prepared.project_ref.identity.value
        return payload

    result = services.current_project_opener.activate(
        prepared,
        opening.context,
        prepare_view=prepare_view,
        expected_active_signature=active_signature(opening.old),
    )

    assert result.is_success, result.diagnostics
    assert result.value["view_payload"] is payload
    assert services.project_lifecycle.active.project_ref == prepared.project_ref
    assert json.loads(pointer.read_bytes())["project_id"] == prepared.project_ref.identity.value
    assert all(baselines is prepared.baselines for _ref, baselines in _catalog(opening, prepared))


def test_failed_open_does_not_overwrite_a_subsequently_replaced_baseline_registration(opening):
    services = opening.services
    prepared = opening.prepare()
    newer_registration = tuple(list(prepared.baselines))
    assert newer_registration is not prepared.baselines
    persisted = opening.persisted()

    def prepare_view(_snapshot):
        services.baselines.replace_many(prepared.project_ref, prepared.variant_refs, newer_registration)
        raise ValueError("view failed after another registration")

    result = services.current_project_opener.activate(prepared, opening.context, prepare_view=prepare_view)

    assert not result.is_success
    assert services.project_lifecycle.active is opening.old
    assert opening.persisted() == persisted
    assert all(baselines is newer_registration for _ref, baselines in _catalog(opening, prepared))


@pytest.mark.parametrize("decision", [DirtyDecision.SAVE, DirtyDecision.DISCARD])
def test_stale_user_decision_preserves_edits_made_while_sources_were_parsed(opening, decision):
    services = opening.services
    key = opening.old.variant.snapshot().entries[0].entry_key
    assert services.gui_project_commands.update_entry(key, opening.context, translation="before parsing").is_success
    expected = active_signature(services.project_lifecycle.active)
    prepared = opening.prepare()
    assert services.gui_project_commands.update_entry(
        key, opening.context, translation="new edit during parsing"
    ).is_success
    previous_catalog = _catalog(opening, prepared)
    persisted = opening.persisted()
    projection = services.project_projection.snapshot()
    seen = []

    result = services.current_project_opener.activate(
        prepared,
        opening.context,
        dirty_decision=decision,
        prepare_view=seen.append,
        expected_active_signature=expected,
    )

    assert result.diagnostics[0].code == "PREPARED_TRANSITION_STALE"
    assert seen == []
    assert services.project_lifecycle.active is opening.old
    assert opening.old.dirty
    assert opening.old.variant.snapshot().entries[0].translation == "new edit during parsing"
    assert services.project_projection.snapshot() is projection
    assert opening.persisted() == persisted
    _assert_catalog_identity(_catalog(opening, prepared), previous_catalog)


def test_external_same_revision_write_during_view_preparation_is_not_overwritten(opening):
    services = opening.services
    prepared = opening.prepare()
    variant_path = Path(services.variants.path_for(prepared.variant_ref))
    previous_catalog = _catalog(opening, prepared)
    pointer = Path(services.root) / "active-project.json"
    pointer_before = pointer.read_bytes()
    projection = services.project_projection.snapshot()
    changed_bytes = []

    def prepare_view(_snapshot):
        document = json.loads(variant_path.read_bytes())
        document["data"]["label_library"] = {"external": {"name": "External edit"}}
        changed_bytes.append(serialize_document(document))
        variant_path.write_bytes(changed_bytes[-1])
        return {"ready": True}

    result = services.current_project_opener.activate(prepared, opening.context, prepare_view=prepare_view)

    assert result.diagnostics[0].code == "PROJECT_ACTIVATION_STALE"
    assert services.project_lifecycle.active is opening.old
    assert services.project_projection.snapshot() is projection
    assert variant_path.read_bytes() == changed_bytes[0]
    assert pointer.read_bytes() == pointer_before
    _assert_catalog_identity(_catalog(opening, prepared), previous_catalog)
