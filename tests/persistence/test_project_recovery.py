from __future__ import annotations

from itertools import count
import json
from pathlib import Path

import pytest

from transbridge.application.contracts import DiagnosticSeverity, RequestContext
from transbridge.application.io import FormatId
from transbridge.application.projects import DirtyDecision, ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.v2 import SCHEMA_VERSION


def _data_tree(root):
    root = Path(root)
    return {str(path.relative_to(root)): path.read_bytes() if path.is_file() else None for path in root.rglob("*")}


def _set_schema(path, version):
    path = Path(path)
    document = json.loads(path.read_bytes())
    document["schema_version"] = version
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def project(tmp_path):
    source = tmp_path / "source.xml"
    raw = Path("tests/contracts/io/fixtures/eet-small.xml").read_bytes()
    source.write_bytes(raw)
    ids = count()
    services = build_persistence_v2_services(
        tmp_path / "d",
        id_factory=lambda: str(next(ids)),
        timestamp_factory=lambda: "2026-08-31T00:00:00+00:00",
    )
    context = RequestContext("test-recovery", run_id="recover")
    created = services.gui_project_commands.create_project(
        ProjectProvisioningRequest("Recovery", source=ProjectSourceRequest(str(source), FormatId.XML_EET)),
        context,
    )
    assert created.is_success
    active = services.project_lifecycle.active
    key = active.variant.snapshot().entries[0].entry_key
    assert services.gui_project_commands.update_entry(key, context, translation="已保存译文", stage=5).is_success
    assert services.gui_project_commands.save(context).is_success
    path = services.projects.path_for(active.project_ref)
    yield services, context, source, raw, path, key
    services.close()


@pytest.mark.parametrize("source_change", ["missing", "changed"])
@pytest.mark.parametrize("project_schema", [2, SCHEMA_VERSION])
@pytest.mark.parametrize("variant_schema", [2, SCHEMA_VERSION])
def test_optional_reference_prepare_preserves_edits_then_activates_the_saved_variant(
    project, source_change, project_schema, variant_schema
):
    services, context, source, raw, path, key = project
    active = services.project_lifecycle.active
    _set_schema(path, project_schema)
    _set_schema(services.variants.path_for(active.formal_variant_ref), variant_schema)
    assert services.gui_project_commands.update_entry(key, context, translation="尚未保存的修改").is_success
    active = services.project_lifecycle.active
    before_baselines = services.baselines.provide(active.project, active.formal_variant_ref, context)
    if source_change == "missing":
        source.rename(source.with_name("moved.xml"))
    else:
        source.write_bytes(raw + b"\n")
    persisted = _data_tree(services.root)

    result = services.current_project_opener.prepare_path(path, context)

    assert result.is_success
    assert result.value.recovery is None
    if variant_schema == SCHEMA_VERSION and project_schema == SCHEMA_VERSION:
        assert not result.value.missing_inputs
    assert result.value.prepared_read.snapshot.entries[0].translation == "已保存译文"
    assert result.value.prepared_read.snapshot.entries[0].stage.value == 5
    assert all(item.severity is DiagnosticSeverity.WARNING for item in result.diagnostics)
    assert services.project_lifecycle.active is active
    assert active.dirty
    assert active.variant.snapshot().entries[0].translation == "尚未保存的修改"
    assert services.baselines.provide(active.project, active.formal_variant_ref, context) is before_baselines
    assert _data_tree(services.root) == persisted
    activated = services.current_project_opener.activate(result.value, context, dirty_decision=DirtyDecision.DISCARD)
    assert activated.is_success and "read_only" not in activated.value, activated.diagnostics
    assert services.project_lifecycle.active.variant.snapshot().entries[0].translation == "已保存译文"


def test_reference_changes_do_not_disable_saved_content(project):
    services, context, source, raw, path, _key = project
    source.write_bytes(raw + b"\n")
    opened = services.current_project_opener.open_path(path, context)
    assert opened.is_success and "read_only" not in opened.value
    assert not opened.value["missing_inputs"]
    assert services.gui_project_commands.save(context).is_success

    source.write_bytes(raw)
    opened = services.current_project_opener.open_path(path, context)

    assert opened.is_success
    assert "recovery" not in opened.value
    assert services.project_lifecycle.active.variant.snapshot().entries[0].translation == "已保存译文"
    assert not services.project_lifecycle.active.dirty


@pytest.mark.parametrize("raw_variant", [b"{}", b"{", b'{"schema_version": 3}'])
def test_source_failure_does_not_hide_an_invalid_saved_variant(project, raw_variant):
    services, context, source, _raw, path, _key = project
    source.rename(source.with_name("moved.xml"))
    variant_path = Path(services.variants.path_for(services.project_lifecycle.active.formal_variant_ref))
    variant_path.write_bytes(raw_variant)
    persisted = _data_tree(services.root)

    result = services.current_project_opener.prepare_path(path, context)

    assert not result.is_success
    assert result.value is None
    assert result.diagnostics[0].code == "VARIANT_RECORD_UNAVAILABLE"
    assert _data_tree(services.root) == persisted


@pytest.mark.parametrize("schema", [2, SCHEMA_VERSION])
def test_invalid_saved_project_is_rejected_without_quarantine_or_migration(project, schema):
    services, context, _source, _raw, path, _key = project
    document = json.loads(Path(path).read_bytes())
    document["schema_version"] = schema
    document["data"]["name"] = None
    Path(path).write_text(json.dumps(document), encoding="utf-8")
    persisted = _data_tree(services.root)

    result = services.current_project_opener.prepare_path(path, context)

    assert not result.is_success
    assert result.diagnostics[0].code == "PROJECT_RECORD_UNAVAILABLE"
    assert _data_tree(services.root) == persisted


@pytest.mark.parametrize("legacy_schema", [2, 3])
def test_normal_prepare_leaves_legacy_records_unchanged_until_activation(project, legacy_schema):
    services, context, _source, _raw, path, _key = project
    variant_path = services.variants.path_for(services.project_lifecycle.active.formal_variant_ref)
    _set_schema(path, legacy_schema)
    _set_schema(variant_path, legacy_schema)
    persisted = _data_tree(services.root)

    prepared = services.current_project_opener.prepare_path(path, context)

    assert prepared.is_success, prepared.diagnostics
    assert prepared.value.recovery is None
    assert _data_tree(services.root) == persisted

    activated = services.current_project_opener.activate(prepared.value, context)

    assert activated.is_success, activated.diagnostics
    assert json.loads(Path(path).read_bytes())["schema_version"] == SCHEMA_VERSION
    assert json.loads(Path(variant_path).read_bytes())["schema_version"] == SCHEMA_VERSION
    assert services.project_lifecycle.active.variant.snapshot().entries[0].translation == "已保存译文"


def test_recovery_does_not_bypass_request_identity(project):
    services, context, source, _raw, path, _key = project
    source.rename(source.with_name("moved.xml"))
    another = RequestContext("test-recovery", run_id="other", project_id="another-project")

    denied = services.current_project_opener.prepare_path(path, another)
    assert not denied.is_success
    assert denied.diagnostics[0].code == "PROJECT_CONTEXT_MISMATCH"

    prepared = services.current_project_opener.prepare_path(path, context)
    denied = services.current_project_opener.activate(prepared.value, another)
    assert not denied.is_success
    assert denied.diagnostics[0].code == "PROJECT_CONTEXT_MISMATCH"


def test_unexpected_source_loader_error_remains_a_failure(project, monkeypatch):
    services, context, _source, _raw, path, _key = project

    def broken_loader(*_args):
        raise RuntimeError("programming error")

    monkeypatch.setattr(services.current_project_opener._source_artifacts, "get", broken_loader)
    result = services.current_project_opener.prepare_path(path, context)
    assert not result.is_success
    assert result.diagnostics[0].code == "INTERNAL_ERROR"


def test_missing_saved_input_does_not_skip_later_available_sources(project, monkeypatch):
    services, context, source, raw, path, _key = project
    other = source.with_name("second.xml")
    other.write_bytes(raw.replace(b"Hello, traveler.", b"Second original"))
    added = services.gui_project_commands.add_source(
        ProjectSourceRequest(str(other), FormatId.XML_EET, options=(("source_namespace", "source:second"),)), context
    )
    assert added.is_success, added.diagnostics
    assert services.gui_project_commands.save(context).is_success
    registrations = services.project_lifecycle.active.project.envelope.data["sources"]
    first_id = registrations[0]["source_artifact_id"]
    calls = []
    get = services.current_project_opener._source_artifacts.get

    def load(artifact_id, **options):
        calls.append(artifact_id)
        if artifact_id == first_id:
            raise FileNotFoundError("unavailable saved input")
        return get(artifact_id, **options)

    monkeypatch.setattr(services.current_project_opener._source_artifacts, "get", load)
    result = services.current_project_opener.prepare_path(path, context)

    assert result.is_success and result.value.recovery is None, result.diagnostics
    assert calls == [item["source_artifact_id"] for item in registrations]
    assert len(result.value.hydrations) == 1 and len(result.value.missing_inputs) == 1
    assert len(result.value.prepared_read.snapshot.entries) == 2
