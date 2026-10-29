from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from transbridge.application.contracts import DomainError, RequestContext
from transbridge.application.io import EntryKey, FormatId, SourceDescriptor, SourceNamespace, SourceSnapshot
from transbridge.application.projects.prepared_source_results import prepare_parsed_source
from transbridge.application.projects.source_registry import (
    BilingualCapability,
    SourceKind,
    SourceRegistration,
    SourceRegistrySnapshot,
)
from transbridge.application.terminology.build import TranslationIoRegisteredSourceParser
from transbridge.application.terminology.input_capture import (
    BuildInputCaptureService,
    CapturedSource,
    TerminologyBaseline,
)
from transbridge.bootstrap.terminology_sources import ProjectSourceLeases, lightweight_build_snapshot
from transbridge.bootstrap.terminology_storage import FormatCapabilities, LifecycleCapture
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.persistence.source_artifact_store import ProjectSourceArtifactStore


def _saved_source(tmp_path):
    source = tmp_path / "source.json"
    source.write_bytes(b"saved source")
    namespace = SourceNamespace("source:retained-original-namespace")
    entry = TranslationEntry("1", "1", "Dragon", "龙", 1, "context", entry_key=EntryKey(namespace, "1"))
    snapshot = SourceSnapshot.from_bytes(SourceDescriptor(str(source)), FormatId.JSON_TRANSBRIDGE, source.read_bytes())
    prepared = prepare_parsed_source(str(source), (entry,), snapshot, FormatId.JSON_TRANSBRIDGE)
    artifact = ProjectSourceArtifactStore(str(tmp_path / "state")).put(prepared)
    registration = SourceRegistration(
        "source-1",
        True,
        FormatId.JSON_TRANSBRIDGE,
        str(source),
        SourceKind.BILINGUAL,
        BilingualCapability.SELF_CONTAINED,
        snapshot.sha256,
        format_options=(("skip_empty", False),),
        source_artifact_id=artifact,
    )
    data = SourceRegistrySnapshot((registration,)).to_project_data()
    lifecycle = SimpleNamespace(active=SimpleNamespace(project=SimpleNamespace(envelope=SimpleNamespace(data=data))))
    return source, registration, lifecycle, snapshot, entry


@pytest.mark.parametrize("clear_reference", [False, True])
def test_project_source_lease_and_parser_use_saved_entries_after_reference_disappears(
    tmp_path, clear_reference, monkeypatch
):
    from transbridge.application.io import default_format_catalog

    source, registration, lifecycle, saved_snapshot, entry = _saved_source(tmp_path)
    source.unlink()
    if clear_reference:
        registration = replace(registration, location="")
        lifecycle.active.project.envelope.data = SourceRegistrySnapshot((registration,)).to_project_data()
    leases = ProjectSourceLeases(tmp_path / "state", lifecycle)
    lease = leases.acquire(registration)
    assert lease.snapshot.size_bytes == saved_snapshot.size_bytes
    capability = FormatCapabilities(default_format_catalog()).capability_for(registration.format_id)
    captured = CapturedSource(
        registration, lease, capability.adapter_id, capability.adapter_version, capability, registration.format_options
    )

    class Io:
        def parse(self, request):
            raise AssertionError("saved terminology source must not reopen its original URI")

    result = TranslationIoRegisteredSourceParser(Io()).parse(captured, RequestContext("test"))

    assert result.entries == (entry.snapshot(),)
    assert result.source_snapshot == saved_snapshot

    def unexpected_read(*_args, **_kwargs):
        raise AssertionError("an unchanged immutable artifact identity must not be read again")

    monkeypatch.setattr(leases._artifacts, "get", unexpected_read)
    assert leases.current_fingerprint(lease) == saved_snapshot.sha256
    changed = replace(registration, source_artifact_id="f" * 64)
    lifecycle.active.project.envelope.data = SourceRegistrySnapshot((changed,)).to_project_data()
    assert leases.current_fingerprint(lease) == ""


def test_saved_source_budget_and_missing_content_do_not_fall_back_to_reference(tmp_path):
    source, registration, lifecycle, saved_snapshot, _entry = _saved_source(tmp_path)
    source.write_bytes(b"changed reference")
    leases = ProjectSourceLeases(tmp_path / "state", lifecycle)
    with pytest.raises(DomainError) as error:
        leases.acquire_bounded(registration, max_bytes=saved_snapshot.size_bytes - 1)
    assert error.value.code == "TERMINOLOGY_STREAMING_REQUIRED"
    with pytest.raises(DomainError) as error:
        leases.acquire(replace(registration, source_artifact_id=None))
    assert error.value.code == "TERMINOLOGY_SOURCE_REQUIRED"
    assert leases.acquire(registration).actual_fingerprint == saved_snapshot.sha256


def test_lightweight_guard_snapshot_preserves_identity_without_retaining_captured_payload(tmp_path):
    from tests.application.terminology.test_build import _snapshot
    from transbridge.application.io import default_format_catalog
    from transbridge.application.terminology.build_freshness import build_input_digest
    from transbridge.application.terminology.in_memory import InMemoryTerminologyRepository
    from transbridge.bootstrap.terminology_storage import (
        ProductionState,
        ProductionTerminologyCommitPort,
        expected_state,
    )

    _path, registration, lifecycle, saved_snapshot, _entry = _saved_source(tmp_path)
    leases = ProjectSourceLeases(tmp_path / "state", lifecycle)
    lease = leases.acquire(registration)
    lease = replace(lease, snapshot=replace(lease.snapshot, metadata=(("localized", (saved_snapshot,)),)))
    capability = FormatCapabilities(default_format_catalog()).capability_for(registration.format_id)
    source = CapturedSource(
        registration, lease, capability.adapter_id, capability.adapter_version, capability, registration.format_options
    )
    original = _snapshot((source,))
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
    assert original.sources[0].lease.snapshot.content == saved_snapshot.content
    assert original.sources[0].lease.snapshot.metadata == (("localized", (saved_snapshot,)),)

    lifecycle.active.project.envelope.identity = original.project_id
    lifecycle.active.project.envelope.revision = original.project_revision
    lifecycle.active.variant = SimpleNamespace(ref=original.variant_snapshot.ref, revision=original.variant_revision)
    state = ProductionState()
    state.put(state.snapshots, expected.digest, original)
    retained = state.get(state.snapshots, expected.digest)
    assert retained == lightweight
    assert retained.sources[0].lease.saved_result is None
    assert retained.sources[0].lease.snapshot.content is None
    assert retained.sources[0].lease.snapshot.metadata == ()
    committed = []
    guard = ProductionTerminologyCommitPort(
        lifecycle, SimpleNamespace(for_project=lambda _project: repository), state, leases
    )
    assert guard.commit_if_current(expected, lambda: committed.append(True)).current
    assert committed == [True]


def test_folded_project_build_retains_merged_saved_translation_without_reference_files(tmp_path, monkeypatch):
    from tests.application.projects.test_source_update_plugin import _plugin
    from transbridge.application.io import default_format_catalog
    from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
    from transbridge.application.terminology.build import TerminologyFullBuilder
    from transbridge.application.terminology.in_memory import InMemoryTerminologyRepository
    from transbridge.application.terminology.models import BuildCompleteness
    from transbridge.bootstrap.history_search import ProjectVariantHistoryProvider
    from transbridge.bootstrap.persistence import build_persistence_v2_services

    original, translated = tmp_path / "original.esp", tmp_path / "translated.esp"
    _plugin(original, [(0x800, "Dragon")])
    _plugin(translated, [(0x800, "Imported translation")])
    root = tmp_path / "state"
    services = build_persistence_v2_services(
        root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "2026-10-28T00:00:00+00:00"
    )
    context = RequestContext("test")
    try:
        created = services.gui_project_commands.create_project(
            ProjectProvisioningRequest(
                "Folded project",
                source=ProjectSourceRequest(str(original), FormatId.PLUGIN_SSE),
                migration_sources=(ProjectSourceRequest(str(translated), FormatId.PLUGIN_SSE),),
            ),
            context,
        )
        assert created.is_success, created.diagnostics
        assert services.project_lifecycle.active.project.envelope.data["source_relations"][0]["folded_into_baseline"]
        original.unlink()
        translated.unlink()
        leases = ProjectSourceLeases(root, services.project_lifecycle)
        artifact_reads = []
        get = leases._artifacts.get

        def counted_get(artifact_id, **options):
            artifact_reads.append(artifact_id)
            return get(artifact_id, **options)

        monkeypatch.setattr(leases._artifacts, "get", counted_get)
        capture = BuildInputCaptureService(
            LifecycleCapture(services.project_lifecycle),
            leases,
            FormatCapabilities(default_format_catalog()),
            SimpleNamespace(capture_baseline=lambda *_args: TerminologyBaseline()),
        ).capture_build_input(context, config={})
        assert capture.is_success, capture.diagnostics
        assert len(artifact_reads) == len(capture.value.sources)

        class Io:
            def parse(self, request):
                raise AssertionError("a folded Project build must use its merged saved artifact")

        outcome = TerminologyFullBuilder(
            TranslationIoRegisteredSourceParser(Io()), InMemoryTerminologyRepository()
        ).build(capture.value, context)

        assert outcome.result.completeness is BuildCompleteness.FULL
        assert {(item.original, item.translation) for item in outcome.result.evidence} == {
            ("Dragon", "Imported translation")
        }
        assert not any(item.startswith("VARIANT_FINGERPRINT_MISMATCH") for item in outcome.result.diagnostics)
        registry = SourceRegistrySnapshot.from_project_data(services.project_lifecycle.active.project.envelope.data)
        history = ProjectVariantHistoryProvider(None, None, None, artifacts=ProjectSourceArtifactStore(str(root)))
        for sources in (registry.sources, tuple(reversed(registry.sources))):
            originals, _registrations, diagnostics = history._originals(
                services.project_lifecycle.active.project.envelope.identity,
                SimpleNamespace(sources=sources, relations=registry.relations),
                None,
            )
            assert set(originals.values()) == {"Dragon"}
            assert not diagnostics
    finally:
        services.close()
