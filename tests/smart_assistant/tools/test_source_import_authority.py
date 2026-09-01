import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.application.io.paratranz_mapping import PARATRANZ_EXTENSION_METADATA
from transbridge.application.projects import ProjectProvisioningRequest
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.smart_assistant.tools.tool_parser import _tool_import_json, _tool_parse_eet
from transbridge.smart_assistant.tools.types import ExecutionContext
from transbridge.ui.context import AppContext


@pytest.fixture
def project(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        "transbridge.ui.context.ParatranzConfig.create_or_load",
        lambda: SimpleNamespace(base_url="https://example.invalid", user_id=1),
    )
    services = build_persistence_v2_services(
        tmp_path / "project", id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now"
    )
    request = RequestContext("fixture", run_id=uuid4().hex, authorized_roots=(str(tmp_path),))
    context = AppContext(
        project_projection=services.project_projection,
        project_commands=services.gui_project_commands,
        runtime_context=request,
    )
    assert services.gui_project_commands.create_project(ProjectProvisioningRequest("Repair"), request).is_success
    source = tmp_path / "source.xml"
    source.write_bytes(Path("tests/contracts/io/fixtures/eet-small.xml").read_bytes())
    yield SimpleNamespace(app=app, services=services, ctx=context, request=request, source=source, root=tmp_path)
    context.close_projection()
    services.close()
    app.processEvents()


def load_source(project):
    result = _tool_parse_eet({"path": str(project.source)}, ExecutionContext(app_context=project.ctx))
    assert result.success, result.message
    return next(iter(project.ctx.collection))


def write_json(project, name, payload):
    path = project.root / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_agent_source_creation_is_saved_with_hydration(project):
    load_source(project)
    assert project.ctx.active_slot.format_id is FormatId.XML_EET
    assert project.ctx.active_slot.source_snapshot is not None
    assert len(project.ctx.project_sources) == 1
    assert not project.ctx.authoritative_projection_diverged()
    assert project.services.gui_project_commands.save(project.request).is_success
    ref = project.services.project_lifecycle.active.formal_variant_ref
    assert len(project.services.variants.load(ref).value.envelope.data["entries"]) == 1


def test_internal_append_commits_existing_complete_entry_key(project):
    entry = load_source(project)
    data = entry.to_dict()
    data.update(translation="Changed", stage=1)
    path = write_json(project, "update.json", [data])
    result = _tool_import_json({"path": path, "action": "append"}, ExecutionContext(app_context=project.ctx))
    assert result.success, result.message
    assert project.services.project_lifecycle.active.variant.snapshot().entries[0].translation == "Changed"
    assert not project.ctx.authoritative_projection_diverged()


def test_foreign_append_does_not_mutate_projection_or_authority(project):
    original = load_source(project).to_dict()
    before = project.services.project_lifecycle.active.variant.snapshot()
    path = write_json(project, "foreign.json", [{"key": "new", "original": "Other", "translation": "Wrong"}])
    result = _tool_import_json({"path": path, "action": "append"}, ExecutionContext(app_context=project.ctx))
    assert not result.success
    assert next(iter(project.ctx.collection)).to_dict() == original
    assert project.services.project_lifecycle.active.variant.snapshot() == before


def test_paratranz_sources_preserve_remote_ids_extensions_and_equal_local_keys(project):
    for index in (1, 2):
        path = write_json(
            project,
            f"source{index}.json",
            [
                {
                    "id": index,
                    "key": "same",
                    "original": f"Original {index}",
                    "translation": f"Value {index}",
                    "note": "preserved",
                    "metadata": {"remote_extension": True},
                }
            ],
        )
        result = _tool_import_json({"path": path, "project_id": 7}, ExecutionContext(app_context=project.ctx))
        assert result.success, result.message
        entry = next(iter(project.ctx.collection))
        assert entry.external_refs[0].opaque_id == index
        assert entry.external_refs[0].scope == "project:7"
        extensions = dict(entry.metadata)[PARATRANZ_EXTENSION_METADATA]
        assert extensions["note"] == "preserved"
        assert extensions["metadata"] == {"remote_extension": True}
    states = project.services.project_lifecycle.active.variant.snapshot().entries
    assert len(states) == 2 and states[0].entry_key != states[1].entry_key
    assert not project.ctx.authoritative_projection_diverged()
    assert project.services.gui_project_commands.save(project.request).is_success
    prepared = project.services.current_project_opener.prepare_active(project.request)
    assert prepared.is_success, prepared.diagnostics
    assert prepared.value.recovery is None
    hydrated = tuple(entry for source in prepared.value.hydrations for entry in source.entries)
    assert {entry.entry_key for entry in hydrated} == {state.entry_key for state in states}
    assert {entry.external_refs[0].opaque_id for entry in hydrated} == {1, 2}


def test_ambiguous_json_requires_choice_without_changing_project(project):
    path = write_json(project, "ambiguous.json", [{"id": "opaque", "key": "key", "original": "Text"}])
    result = _tool_import_json({"path": path}, ExecutionContext(app_context=project.ctx))
    assert not result.success and "format" in result.message
    assert not project.ctx.slots and not project.ctx.project_sources


def test_duplicate_source_failure_leaves_existing_slot_unchanged(project):
    load_source(project)
    slot = project.ctx.active_slot
    result = _tool_parse_eet({"path": str(project.source)}, ExecutionContext(app_context=project.ctx))
    assert not result.success
    assert project.ctx.active_slot is slot
    assert len(project.ctx.project_sources) == 1
