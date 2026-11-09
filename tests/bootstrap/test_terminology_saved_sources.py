from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests.application.projects.test_source_update_plugin import _plugin
from transbridge.application.contracts import DomainError, RequestContext
from transbridge.application.io import FormatId, default_format_catalog
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.application.projects.source_content import source_content_identity
from transbridge.application.projects.source_registry import SourceRegistrySnapshot
from transbridge.application.terminology.build import TerminologyFullBuilder, TranslationIoRegisteredSourceParser
from transbridge.application.terminology.build_freshness import build_input_digest
from transbridge.application.terminology.in_memory import InMemoryTerminologyRepository
from transbridge.application.terminology.input_capture import BuildInputCaptureService, TerminologyBaseline
from transbridge.application.terminology.models import BuildCompleteness
from transbridge.bootstrap.history_search import ProjectVariantHistoryProvider
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.bootstrap.terminology_sources import ProjectSourceLeases, lightweight_build_snapshot
from transbridge.bootstrap.terminology_storage import (
    FormatCapabilities,
    LifecycleCapture,
    ProductionState,
    ProductionTerminologyCommitPort,
    expected_state,
)
from transbridge.persistence.project_store.repository import ProjectStore


@pytest.fixture
def services(tmp_path):
    storage = build_persistence_v2_services(
        tmp_path / "state",
        id_factory=lambda: uuid4().hex,
        timestamp_factory=lambda: "2026-10-28T00:00:00+00:00",
    )
    try:
        yield storage
    finally:
        storage.close()


@pytest.fixture
def saved_source(tmp_path, services):
    source = tmp_path / "source.esp"
    _plugin(source, [(0x800, "Dragon")])
    commands = services.gui_project_commands
    context = RequestContext("test", run_id="save-terminology-source")
    created = commands.create_project(
        ProjectProvisioningRequest("Saved project", source=ProjectSourceRequest(str(source), FormatId.PLUGIN_SSE)),
        context,
    )
    assert created.is_success, created.diagnostics
    active = services.project_lifecycle.active
    (registration,) = SourceRegistrySnapshot.from_project_data(active.project.envelope.data).sources
    (entry,) = active.variant.snapshot().entries
    updated = commands.update_entry(entry.entry_key, context, translation="龙", stage=1)
    assert updated.is_success, updated.diagnostics
    saved = commands.save(context)
    assert saved.is_success, saved.diagnostics
    return source, registration, entry.entry_key, context


class _NoExternalRead:
    def parse(self, request):
        raise AssertionError(f"saved terminology source must not reopen {request.source.uri}")


def _capture(services, leases, context):
    result = BuildInputCaptureService(
        LifecycleCapture(services.project_lifecycle),
        leases,
        FormatCapabilities(default_format_catalog()),
        SimpleNamespace(capture_baseline=lambda *_args: TerminologyBaseline()),
    ).capture_build_input(context, config={})
    assert result.is_success, result.diagnostics
    return result.value


@pytest.mark.parametrize("source_change", ["removed", "replaced"])
def test_project_source_lease_and_parser_use_saved_entries_after_reference_changes(
    services, saved_source, source_change, monkeypatch
):
    source, registration, key, context = saved_source
    if source_change == "removed":
        source.unlink()
    else:
        _plugin(source, [(0x800, "Different text on disk")])

    assert registration.location == ""
    assert registration.source_artifact_id is None
    leases = ProjectSourceLeases(services.root, services.project_lifecycle)
    (captured,) = _capture(services, leases, context).sources
    result = TranslationIoRegisteredSourceParser(_NoExternalRead()).parse(captured, context)
    (entry,) = result.entries
    assert (entry.entry_key, entry.original, entry.translation, entry.stage) == (key, "Dragon", "龙", 1)
    assert result.source_snapshot == captured.lease.snapshot
    assert result.source_snapshot.source.uri.startswith("project-source:")
    assert result.source_snapshot.content is None

    def unexpected_read(*_args, **_kwargs):
        raise AssertionError("checking a captured source fingerprint must not reload its entries")

    with monkeypatch.context() as patch:
        patch.setattr(ProjectStore, "iter_source_entries", unexpected_read)
        assert leases.current_fingerprint(captured.lease) == registration.fingerprint
    removed = services.gui_project_commands.remove_source(registration.source_id, context)
    assert removed.is_success, removed.diagnostics
    assert leases.current_fingerprint(captured.lease) == ""


def test_saved_source_budget_and_missing_content_do_not_fall_back_to_reference(services, saved_source):
    source, registration, _key, _context = saved_source
    _plugin(source, [(0x800, "Changed reference")])
    leases = ProjectSourceLeases(services.root, services.project_lifecycle)
    saved = leases.acquire(registration)
    with pytest.raises(DomainError) as error:
        leases.acquire_bounded(registration, max_bytes=saved.snapshot.size_bytes - 1)
    assert error.value.code == "TERMINOLOGY_STREAMING_REQUIRED"
    missing = replace(registration, location=str(source), legacy=(("namespace", "source:missing"),))
    with pytest.raises(DomainError) as error:
        leases.acquire(missing)
    assert error.value.code == "TERMINOLOGY_SOURCE_REQUIRED"
    assert saved.actual_fingerprint == registration.fingerprint


def test_lightweight_guard_snapshot_preserves_identity_without_retaining_captured_payload(services, saved_source):
    _path, _registration, _key, context = saved_source
    leases = ProjectSourceLeases(services.root, services.project_lifecycle)
    original = _capture(services, leases, context)
    repository = InMemoryTerminologyRepository()
    expected = expected_state(original, repository)

    lightweight = lightweight_build_snapshot(original)

    assert lightweight.canonical_payload() == original.canonical_payload()
    assert build_input_digest(lightweight) == build_input_digest(original)
    assert expected_state(lightweight, repository) == expected
    assert lightweight.sources[0].lease.saved_result is None
    assert lightweight.sources[0].lease.snapshot.content is None
    assert lightweight.sources[0].lease.snapshot.metadata == ()
    assert original.sources[0].lease.saved_result is not None
    assert original.sources[0].lease.snapshot.metadata

    state = ProductionState()
    state.put(state.snapshots, expected.digest, original)
    assert state.get(state.snapshots, expected.digest) == lightweight
    committed = []
    guard = ProductionTerminologyCommitPort(
        services.project_lifecycle, SimpleNamespace(for_project=lambda _project: repository), state, leases
    )
    assert guard.commit_if_current(expected, lambda: committed.append(True)).current
    assert committed == [True]


def test_folded_project_build_retains_merged_saved_translation_without_reference_files(tmp_path, services, monkeypatch):
    original, translated = tmp_path / "original.esp", tmp_path / "translated.esp"
    _plugin(original, [(0x800, "Dragon")])
    _plugin(translated, [(0x800, "Imported translation")])
    context = RequestContext("test")
    created = services.gui_project_commands.create_project(
        ProjectProvisioningRequest(
            "Folded project",
            source=ProjectSourceRequest(str(original), FormatId.PLUGIN_SSE),
            migration_sources=(ProjectSourceRequest(str(translated), FormatId.PLUGIN_SSE),),
        ),
        context,
    )
    assert created.is_success, created.diagnostics
    active = services.project_lifecycle.active
    registry = SourceRegistrySnapshot.from_project_data(active.project.envelope.data)
    (relation,) = registry.relations
    assert relation.folded_into_baseline
    primary = next(source for source in registry.sources if source.source_id == relation.to_source_id)
    original.unlink()
    translated.unlink()
    leases = ProjectSourceLeases(services.root, services.project_lifecycle)
    reads = []
    read = ProjectStore.iter_source_entries

    def counted_read(store, variant, namespace, **options):
        reads.append(namespace.value)
        return read(store, variant, namespace, **options)

    with monkeypatch.context() as patch:
        patch.setattr(ProjectStore, "iter_source_entries", counted_read)
        captured = _capture(services, leases, context)
    assert reads == [source_content_identity(primary.to_dict())]
    assert {source.registration.source_id for source in captured.sources} == {
        item.source_id for item in registry.sources
    }

    outcome = TerminologyFullBuilder(
        TranslationIoRegisteredSourceParser(_NoExternalRead()), InMemoryTerminologyRepository()
    ).build(captured, context)
    assert outcome.result.completeness is BuildCompleteness.FULL
    assert {(item.original, item.translation) for item in outcome.result.evidence} == {
        ("Dragon", "Imported translation")
    }
    assert not any(item.startswith("VARIANT_FINGERPRINT_MISMATCH") for item in outcome.result.diagnostics)

    history = ProjectVariantHistoryProvider(services.project_catalog, services.projects).collect(None)
    assert [(record.original, record.translation) for record in history.records] == [("Dragon", "Imported translation")]
    assert not history.diagnostics
