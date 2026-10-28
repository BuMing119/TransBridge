"""Persist unchanged parsed sources once while retaining final source contracts."""

import pytest

from tests.application.projects.test_source_commands import _setup
from tests.persistence.v2.fakes import MemoryFilesystem
from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId, SourceDescriptor, SourceSnapshot
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.application.projects.prepared_source_results import prepare_parsed_source
from transbridge.application.projects.provisioning_sources import prepare_project_sources
from transbridge.application.projects.source_commands import source_request_with_initial_entry_states
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.persistence.saved_project_sources import SavedProjectSourcePreparer
from transbridge.persistence.source_artifact_store import ProjectSourceArtifactStore


class _CountingArtifacts(ProjectSourceArtifactStore):
    def __init__(self, root):
        super().__init__(str(root), MemoryFilesystem())
        self.put_count = 0

    def put(self, prepared):
        self.put_count += 1
        return super().put(prepared)


class _ParsedSources:
    def __init__(self, prepared):
        self.prepared = {item.to_dict()["location"]: item for item in prepared}

    def prepare_source(self, request, context, *, role, common_options):
        return self.prepared[request.location]


def _prepared(path, namespace, *, original="Original", role="primary"):
    path = str(path)
    entry = TranslationEntry(
        "legacy-entry",
        "entry",
        original,
        "",
        0,
        "FULL",
        entry_key=EntryKey(SourceNamespace(namespace), "entry"),
        metadata=(("nested", {"values": ["saved"]}),),
    )
    snapshot = SourceSnapshot.from_bytes(SourceDescriptor(path), FormatId.PLUGIN_SSE, path.encode())
    return prepare_parsed_source(path, (entry,), snapshot, FormatId.PLUGIN_SSE, role=role)


def _stored(artifacts, descriptor):
    return artifacts.get(descriptor["source_artifact_id"], location=descriptor["location"])


def test_provisioning_persists_each_unchanged_source_once(tmp_path):
    prepared = tuple(_prepared(tmp_path / f"source-{index}.esp", f"source:{index}") for index in range(3))
    artifacts = _CountingArtifacts(tmp_path / "store")
    sources = SavedProjectSourcePreparer(_ParsedSources(prepared), artifacts)
    request = ProjectProvisioningRequest(
        "Sources", additional_sources=tuple(ProjectSourceRequest(item.to_dict()["location"]) for item in prepared)
    )

    result = prepare_project_sources(sources, request, RequestContext("test"))

    assert artifacts.put_count == len(prepared)
    for actual, expected in zip(result.registrations, prepared, strict=True):
        restored = _stored(artifacts, actual.to_dict())
        assert restored.baseline == expected.baseline
        assert restored.hydration == expected.hydration


def test_provisioning_republishes_only_folded_primary_and_saves_final_values(tmp_path):
    primary = _prepared(tmp_path / "primary.esp", "source:fold")
    migration = _prepared(tmp_path / "translated.esp", "source:fold", original="Translated", role="migration")
    artifacts = _CountingArtifacts(tmp_path / "store")
    sources = SavedProjectSourcePreparer(_ParsedSources((primary, migration)), artifacts)
    request = ProjectProvisioningRequest(
        "Folded",
        source=ProjectSourceRequest(primary.to_dict()["location"]),
        migration_sources=(ProjectSourceRequest(migration.to_dict()["location"]),),
    )

    result = prepare_project_sources(sources, request, RequestContext("test"))

    assert artifacts.put_count == 3
    restored = _stored(artifacts, result.registrations[0].to_dict())
    assert restored.baseline == result.baselines[0]
    assert restored.hydration == result.hydrations[0]
    assert restored.baseline.entries[0].translation == "Translated"
    assert restored.hydration.entries[0].translation == "Translated"


def test_add_source_persists_unchanged_parse_once(tmp_path):
    service, lifecycle, _registry, _project_ref, _variant_ref, context = _setup()
    prepared = _prepared(tmp_path / "added.esp", "source:added")
    artifacts = _CountingArtifacts(tmp_path / "store")
    service._preparer = SavedProjectSourcePreparer(_ParsedSources((prepared,)), artifacts)

    added = service.add_source(ProjectSourceRequest(prepared.to_dict()["location"]), context)

    assert added.is_success, added.diagnostics
    assert artifacts.put_count == 1
    restored = _stored(artifacts, lifecycle.active.project.envelope.data["sources"][0])
    assert restored.baseline.entries == lifecycle.active.variant.snapshot().entries
    assert restored.hydration == added.value.hydration


def test_add_source_persists_final_initial_translation_states(tmp_path):
    service, lifecycle, _registry, _project_ref, _variant_ref, context = _setup()
    prepared = _prepared(tmp_path / "imported.esp", "source:imported")
    artifacts = _CountingArtifacts(tmp_path / "store")
    service._preparer = SavedProjectSourcePreparer(_ParsedSources((prepared,)), artifacts)
    request = source_request_with_initial_entry_states(
        ProjectSourceRequest(prepared.to_dict()["location"]), {"entry": ("Imported", 3)}
    )

    added = service.add_source(request, context)

    assert added.is_success, added.diagnostics
    assert artifacts.put_count == 2
    restored = _stored(artifacts, lifecycle.active.project.envelope.data["sources"][0])
    assert restored.baseline.entries == lifecycle.active.variant.snapshot().entries
    assert restored.hydration == added.value.hydration
    assert restored.baseline.entries[0].translation == "Imported"
    assert restored.hydration.entries[0].translation == "Imported"


def test_public_add_prepared_source_republishes_changed_metadata_despite_existing_reference(tmp_path):
    service, lifecycle, _registry, _project_ref, _variant_ref, context = _setup()
    original = _prepared(tmp_path / "external.esp", "source:external")
    artifacts = _CountingArtifacts(tmp_path / "store")
    service._preparer = SavedProjectSourcePreparer(_ParsedSources((original,)), artifacts)
    prepared = service._preparer.prepare_source(
        ProjectSourceRequest(original.to_dict()["location"]), context, role="primary", common_options=()
    )
    previous_id = prepared.to_dict()["source_artifact_id"]
    dict(prepared.hydration.entries[0].metadata)["nested"]["values"].append("changed by caller")

    added = service.add_prepared_source(prepared, context)

    assert added.is_success, added.diagnostics
    assert artifacts.put_count == 2
    registration = lifecycle.active.project.envelope.data["sources"][0]
    assert registration["source_artifact_id"] != previous_id
    restored = _stored(artifacts, registration)
    assert dict(restored.hydration.entries[0].metadata)["nested"]["values"] == ["saved", "changed by caller"]
    previous = artifacts.get(previous_id)
    assert dict(previous.hydration.entries[0].metadata)["nested"]["values"] == ["saved"]


@pytest.mark.parametrize("external_prepared", [False, True])
def test_source_publication_retains_expected_revision_guard(tmp_path, external_prepared):
    service, lifecycle, _registry, _project_ref, _variant_ref, context = _setup()
    prepared = _prepared(tmp_path / "stale.esp", "source:stale")
    artifacts = _CountingArtifacts(tmp_path / "store")
    service._preparer = SavedProjectSourcePreparer(_ParsedSources((prepared,)), artifacts)
    previous = lifecycle.active
    if external_prepared:
        result = service.add_prepared_source(prepared, context, expected_project_revision=1)
    else:
        result = service.add_source(
            ProjectSourceRequest(prepared.to_dict()["location"]), context, expected_project_revision=1
        )

    assert not result.is_success
    assert result.diagnostics[0].code == "ACTIVE_PROJECT_REVISION_CHANGED"
    assert lifecycle.active is previous
    assert artifacts.put_count == (0 if external_prepared else 1)
