from dataclasses import replace
import json
from pathlib import Path
from uuid import uuid4

import pytest

from transbridge.application.contracts import DiagnosticSeverity, RequestContext
from transbridge.application.io import EntryKey, FormatId
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.v2 import ProjectRef


def _services(root):
    return build_persistence_v2_services(
        root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "2026-10-28T00:00:00+00:00"
    )


@pytest.fixture
def saved_inputs(tmp_path):
    root = tmp_path / "store"
    context = RequestContext("source-inputs", run_id="open-editable")
    raw = Path("tests/contracts/io/fixtures/eet-small.xml").read_text(encoding="utf-8")
    paths = (tmp_path / "first.xml", tmp_path / "second.xml")
    paths[0].write_text(raw, encoding="utf-8")
    paths[1].write_text(raw.replace("Hello, traveler.", "Another original"), encoding="utf-8")
    services = _services(root)
    try:
        created = services.gui_project_commands.create_project(
            ProjectProvisioningRequest(
                "Editable source inputs",
                source=ProjectSourceRequest(
                    str(paths[0]), FormatId.XML_EET, options=(("source_namespace", "source:first"),)
                ),
                additional_sources=(
                    ProjectSourceRequest(
                        str(paths[1]), FormatId.XML_EET, options=(("source_namespace", "source:second"),)
                    ),
                ),
            ),
            context,
        )
        assert created.is_success, created.diagnostics
        active = services.project_lifecycle.active
        for entry in active.variant.snapshot().entries:
            changed = services.gui_project_commands.update_entry(
                entry.entry_key, context, translation=f"saved-{entry.entry_key.namespace.value}", stage=5
            )
            assert changed.is_success, changed.diagnostics
        assert services.gui_project_commands.save(context).is_success
        main = services.project_lifecycle.active.variant.snapshot()
        assert services.gui_project_commands.create_variant("Branch", context, copy_active=True).is_success
        assert services.gui_project_commands.save(context).is_success
        active = services.project_lifecycle.active
        yield root, context, Path(services.projects.path_for(active.project_ref)), main, active.variant.snapshot()
    finally:
        services.close()


@pytest.mark.parametrize("missing_count", [1, 2])
def test_missing_sources_activate_complete_saved_variant_and_allow_edit_save_and_switch(saved_inputs, missing_count):
    root, context, project_path, main, branch = saved_inputs
    document = json.loads(project_path.read_bytes())
    for index, source in enumerate(document["data"]["sources"][:missing_count]):
        source["source_artifact_id"] = ("f" if index == 0 else "e") * 64
        source["location"] = ""
        source["legacy"]["path"] = ""
    project_path.write_text(json.dumps(document), encoding="utf-8")
    services = _services(root)
    try:
        prepared = services.current_project_opener.prepare_path(project_path, context)
        assert prepared.is_success and prepared.value.recovery is None, prepared.diagnostics
        assert len(prepared.value.hydrations) == 2 - missing_count
        assert len(prepared.value.baselines) == 2 - missing_count
        assert len(prepared.value.missing_inputs) == missing_count
        assert all(item.severity is DiagnosticSeverity.WARNING for item in prepared.diagnostics)
        assert all(
            item.diagnostic.code == "PROJECT_SOURCE_ARTIFACT_UNAVAILABLE" for item in prepared.value.missing_inputs
        )
        assert prepared.value.prepared_read.snapshot == branch
        opened = services.current_project_opener.activate(prepared.value, context)
        assert opened.is_success and "read_only" not in opened.value, opened.diagnostics
        active = services.project_lifecycle.active
        assert active.variant.snapshot() == branch
        projection = services.project_projection.snapshot()
        assert len(projection.values["entries"]) == len(branch.entries)
        assert len(projection.values["missing_inputs"]) == missing_count
        missing_namespace = prepared.value.missing_inputs[0].namespace
        entry = next(item for item in branch.entries if item.entry_key.namespace.value == missing_namespace)
        assert services.gui_project_commands.update_entry(
            entry.entry_key, context, translation="edited without source"
        ).is_success
        assert services.gui_project_commands.save(context).is_success
        updated = active.variant.snapshot()
        assert updated.source_fingerprints == branch.source_fingerprints
        copied = services.gui_project_commands.create_variant("Copied without source", context, copy_active=True)
        assert copied.is_success, copied.diagnostics
        assert services.project_lifecycle.active.variant.snapshot().entries == updated.entries
        assert len(services.project_lifecycle.active.missing_inputs) == missing_count
        assert services.gui_project_commands.switch_v2(ProjectRef(main.ref.project_id), main.ref, context).is_success
        assert services.project_lifecycle.active.variant.snapshot() == main
        assert len(services.project_lifecycle.active.missing_inputs) == missing_count
        assert services.gui_project_commands.switch_v2(
            ProjectRef(branch.ref.project_id), branch.ref, context
        ).is_success
        assert services.project_lifecycle.active.variant.snapshot().entries == updated.entries
        assert len(services.project_lifecycle.active.missing_inputs) == missing_count
    finally:
        services.close()


def test_unregistered_legacy_namespace_remains_editable_without_invented_fingerprint(saved_inputs):
    root, context, project_path, _main, branch = saved_inputs
    document = json.loads(project_path.read_bytes())
    document["data"]["sources"] = []
    document["data"]["legacy"] = {"archive_recovery": True}
    project_path.write_text(json.dumps(document), encoding="utf-8")
    services = _services(root)
    variant_path = Path(services.variants.path_for(branch.ref))
    services.close()
    variant = json.loads(variant_path.read_bytes())
    # Retain a real saved row, with precisely the unknown identity carried by V1 migration.
    variant["data"]["entries"] = variant["data"]["entries"][:1]
    variant["data"]["entries"][0]["entry_key"]["namespace"] = "legacy:v1"
    variant["data"]["source_fingerprints"] = [{"namespace": "legacy:v1", "sha256": None}]
    variant_path.write_text(json.dumps(variant), encoding="utf-8")
    services = _services(root)
    try:
        opened = services.current_project_opener.open_path(project_path, context)
        assert opened.is_success and "read_only" not in opened.value, opened.diagnostics
        active = services.project_lifecycle.active
        before = active.variant.snapshot()
        assert before.source_fingerprints[0].sha256 is None
        assert active.missing_inputs[0].source_id is None
        assert active.missing_inputs[0].namespace == "legacy:v1"
        assert services.baselines.provide(active.project, active.formal_variant_ref, context) == ()
        key = before.entries[0].entry_key
        assert services.gui_project_commands.update_entry(key, context, translation="legacy edit").is_success
        assert services.gui_project_commands.save(context).is_success
        reopened = services.current_project_opener.open_path(project_path, context)
        assert reopened.is_success, reopened.diagnostics
        after = services.project_lifecycle.active.variant.snapshot()
        assert after.source_fingerprints == before.source_fingerprints
        assert after.entries[0].entry_key == key and after.entries[0].translation == "legacy edit"
    finally:
        services.close()


@pytest.mark.parametrize("tombstone", [False, True])
def test_available_source_with_uncovered_saved_row_preserves_the_row_and_marks_its_namespace(saved_inputs, tombstone):
    root, context, project_path, _main, branch = saved_inputs
    services = _services(root)
    try:
        entry = branch.entries[0]
        orphan = replace(
            entry,
            entry_key=EntryKey(entry.entry_key.namespace, "saved-orphan"),
            translation="orphan edit",
            tombstone=tombstone,
        )
        snapshot = replace(branch, entries=(*branch.entries, orphan))
        Path(services.variants.path_for(branch.ref)).write_text(
            json.dumps(snapshot.to_dto().envelope.to_dict()), encoding="utf-8"
        )
        opened = services.current_project_opener.open_path(project_path, context)
        assert opened.is_success, opened.diagnostics
        assert len(opened.value["hydrations"]) == 2
        assert {item.namespace for item in opened.value["missing_inputs"]} == (
            set() if tombstone else {entry.entry_key.namespace.value}
        )
        assert services.project_lifecycle.active.variant.snapshot() == snapshot
        assert services.gui_project_commands.update_entry(
            orphan.entry_key, context, translation="still editable"
        ).is_success
        assert services.gui_project_commands.save(context).is_success
    finally:
        services.close()


@pytest.mark.parametrize("entity", ["project", "variant"])
def test_structurally_invalid_formal_records_remain_rejected(saved_inputs, entity):
    root, context, project_path, _main, branch = saved_inputs
    services = _services(root)
    try:
        target = project_path if entity == "project" else Path(services.variants.path_for(branch.ref))
        document = json.loads(target.read_bytes())
        document["data"]["name" if entity == "project" else "entries"] = None
        target.write_text(json.dumps(document), encoding="utf-8")
        before = target.read_bytes()
        prepared = services.current_project_opener.prepare_path(project_path, context)
        assert not prepared.is_success
        assert prepared.diagnostics[0].code == f"{entity.upper()}_RECORD_UNAVAILABLE"
        assert services.project_lifecycle.active is None
        assert target.read_bytes() == before
    finally:
        services.close()
