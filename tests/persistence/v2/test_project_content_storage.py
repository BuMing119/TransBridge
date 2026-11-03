"""Current Projects explicitly own their captured content or disclose missing input."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from tests.application.projects.test_source_artifact_publication_cost import _prepared
from tests.persistence.v2 import test_saved_project_sources as saved_support
from tests.persistence.v2.fakes import MemoryFilesystem
from transbridge.application.contracts import DomainError
from transbridge.application.io import FormatId
from transbridge.application.projects.source_registry import BilingualCapability, SourceKind, SourceRegistration
from transbridge.persistence.saved_project_sources import SavedProjectSourcePreparer
from transbridge.persistence.source_artifact_store import ProjectSourceArtifactStore
from transbridge.persistence.v2 import SCHEMA_VERSION, LoadedRecord, ProjectId, ProjectRef, ProjectRepository
from transbridge.persistence.v2.migration import migrate_to_current
from transbridge.persistence.v2.models import SchemaValidationError
from transbridge.persistence.v2.schema import validate_v2

saved = saved_support.saved
_services = saved_support._services


def _project_document(artifact_id):
    ref = ProjectRef(ProjectId("project-content"))
    registration = SourceRegistration(
        "source-content",
        True,
        FormatId.XML_EET,
        "C:/external/mutated.xml",
        SourceKind.BILINGUAL,
        BilingualCapability.SELF_CONTAINED,
        fingerprint="a" * 64,
        source_artifact_id=artifact_id,
    ).to_dict()
    registration.pop("content_state")
    return ref, {
        "schema_version": 4,
        "entity_type": "project",
        "id": ref.identity.value,
        "revision": 7,
        "data": {
            "name": "Captured content",
            "sources": [registration],
            "source_relations": [],
            "variant_ids": [],
            "active_variant_id": None,
        },
    }


@pytest.mark.parametrize("artifact_id", [None, "b" * 64])
def test_v4_upgrade_declares_only_existing_capture_and_keeps_verified_original(artifact_id, tmp_path):
    ref, document = _project_document(artifact_id)
    before = deepcopy(document)
    filesystem = MemoryFilesystem()
    repository = ProjectRepository(str(tmp_path), filesystem)
    raw = json.dumps(document).encode()
    filesystem.seed(repository.path_for(ref), raw)

    loaded = repository.load(ref)

    assert isinstance(loaded, LoadedRecord)
    assert document == before
    assert loaded.value.envelope.schema_version == SCHEMA_VERSION
    source = loaded.value.envelope.data["sources"][0]
    assert source["content_state"] == ("captured" if artifact_id else "missing")
    assert source.get("source_artifact_id") == artifact_id
    assert filesystem.read_bytes(loaded.migration_report.backup_path) == raw
    assert not any("mutated.xml" in path for _operation, path in filesystem.calls)


def test_current_schema_requires_explicit_content_state_and_consistent_reference():
    ref, document = _project_document("b" * 64)
    current = migrate_to_current(document, ref).document
    validate_v2(current, ref)
    for mutation in ("missing_state", "missing_reference", "contradictory_state"):
        invalid = deepcopy(current)
        registration = invalid["data"]["sources"][0]
        if mutation == "missing_state":
            registration.pop("content_state")
        elif mutation == "missing_reference":
            registration.pop("source_artifact_id")
        else:
            registration["content_state"] = "missing"
        with pytest.raises(SchemaValidationError):
            validate_v2(invalid, ref)


def test_source_without_captured_hydration_cannot_be_published(tmp_path):
    prepared = replace(_prepared(tmp_path / "source.esp", "source:missing"), hydration=None)
    preparer = SavedProjectSourcePreparer(None, None)

    with pytest.raises(DomainError) as caught:
        preparer.persist_prepared_source(prepared)

    assert caught.value.code == "PROJECT_SOURCE_CONTENT_REQUIRED"


def test_translation_and_clean_saves_never_reload_or_republish_source_content(saved, monkeypatch):
    root, source, path, context, _expected = saved
    source.unlink()
    services = _services(root)
    try:
        opened = services.current_project_opener.open_path(path, context)
        assert opened.is_success, opened.diagnostics
        records = {item: item.read_bytes() for item in (root / "source_artifacts").rglob("*") if item.is_file()}

        def forbidden(*_args, **_kwargs):
            pytest.fail("Incremental saves must reuse captured content without rereading or re-encoding it")

        monkeypatch.setattr(ProjectSourceArtifactStore, "get", forbidden)
        monkeypatch.setattr(ProjectSourceArtifactStore, "put", forbidden)
        active = services.project_lifecycle.active
        key = active.variant.snapshot().entries[0].entry_key
        assert services.gui_project_commands.update_entry(key, context, translation="Saved offline").is_success
        assert services.gui_project_commands.save(context).is_success
        assert services.gui_project_commands.save(context).is_success
        persisted = services.variants.read_snapshot(active.formal_variant_ref)
        assert any(row["translation"] == "Saved offline" for row in persisted.envelope.data["entries"])
        assert {item: item.read_bytes() for item in records} == records
        assert services.project_lifecycle.active.project.envelope.data["sources"][0]["content_state"] == "captured"
    finally:
        services.close()


def test_new_blank_variant_is_created_after_missing_reference_is_repaired(saved):
    root, _source, path, context, _expected = saved
    document = json.loads(path.read_bytes())
    document["data"]["sources"][0]["source_artifact_id"] = "f" * 64
    path.write_text(json.dumps(document), encoding="utf-8")
    services = _services(root)
    try:
        assert services.current_project_opener.open_path(path, context).is_success
        assert services.gui_project_commands.create_variant("Empty", context, copy_active=False).is_success
        assert not services.project_lifecycle.active.missing_inputs
        result = services.gui_project_commands.save(context)
        assert result.is_success, result.diagnostics
    finally:
        services.close()
