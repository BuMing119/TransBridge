from __future__ import annotations

from dataclasses import dataclass

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import (
    CapabilityLevel,
    FormatCapability,
    FormatCapabilitySnapshot,
    FormatId,
    SourceDescriptor,
    SourceSnapshot,
)
from transbridge.application.projects.source_registry import (
    BilingualCapability,
    SourceKind,
    SourceRegistration,
    SourceRegistrySnapshot,
)
from transbridge.application.terminology.input_capture import (
    BuildInputCaptureService,
    ProjectVariantCapture,
    SourceLease,
    TerminologyBaseline,
)
from transbridge.persistence.v2.ids import EntityKind, ProjectId, VariantId, VariantRef
from transbridge.persistence.v2.models import ProjectDto, SchemaEnvelope
from transbridge.persistence.v2.variant import VariantSnapshot


def _capability() -> FormatCapabilitySnapshot:
    capability = FormatCapability(read=CapabilityLevel.SUPPORTED)
    return FormatCapabilitySnapshot(
        FormatId.PLUGIN_SSE,
        capability,
        capability,
        "adapter.plugin",
        "3.0",
    )


def _state(registry: SourceRegistrySnapshot) -> ProjectVariantCapture:
    ref = VariantRef(VariantId("main"), ProjectId("project-1"))
    data = {
        "name": "Project",
        **registry.to_project_data(),
        "variant_ids": ["main"],
        "active_variant_id": "main",
    }
    return ProjectVariantCapture(
        ProjectDto(SchemaEnvelope(3, EntityKind.PROJECT, "project-1", 7, data)),
        VariantSnapshot(ref, (), (), revision=9),
    )


@dataclass
class _Projects:
    state: ProjectVariantCapture | None

    def capture_project_variant(self):
        return self.state


class _Leases:
    def __init__(self, *, stale: bool = False) -> None:
        self.stale = stale
        self.acquired = []

    def acquire(self, registration):
        self.acquired.append(registration.source_id)
        snapshot = SourceSnapshot.from_bytes(
            SourceDescriptor(registration.location),
            registration.format_id,
            b"TES4-test",
        )
        return SourceLease(registration.source_id, snapshot, snapshot.sha256)

    def current_fingerprint(self, lease):
        return "0" * 64 if self.stale else lease.actual_fingerprint


class _Capabilities:
    def capability_for(self, format_id):
        return _capability()


class _Baselines:
    def capture_baseline(self, project_id, variant_id):
        return TerminologyBaseline(
            effective_version_id="version-2",
            effective_content_digest="1" * 64,
            draft_id="draft-3",
            draft_base_content_digest="2" * 64,
            draft_revision=4,
            decision_digest="3" * 64,
        )


def _registry() -> SourceRegistrySnapshot:
    return SourceRegistrySnapshot((
        SourceRegistration(
            "source-plugin",
            True,
            FormatId.PLUGIN_SSE,
            "C:/mods/base.esp",
            SourceKind.PLUGIN,
            BilingualCapability.NONE,
            format_options=(("language", "english"), ("discover_sibling_strings", True)),
        ),
    ))


def test_capture_pins_authoritative_state_and_disables_plugin_sibling_discovery() -> None:
    service = BuildInputCaptureService(_Projects(_state(_registry())), _Leases(), _Capabilities(), _Baselines())

    result = service.capture_build_input(
        RequestContext("test", project_id="project-1", variant_id="main", run_id="run-1"),
        config={"normalization": "v1"},
    )

    assert result.outcome is OperationOutcome.COMPLETED and result.value is not None
    assert result.value.project_revision == 7 and result.value.variant_revision == 9
    assert dict(result.value.sources[0].parse_options)["discover_sibling_strings"] is False
    assert result.value.effective_version_id == "version-2"
    assert result.value.effective_content_digest == "1" * 64
    assert result.value.draft_base_content_digest == "2" * 64
    assert result.value.decision_digest == "3" * 64
    assert result.value.canonical_payload()["draft"]["draft_id"] == "draft-3"


def test_capture_rejects_source_changed_after_lease() -> None:
    service = BuildInputCaptureService(
        _Projects(_state(_registry())),
        _Leases(stale=True),
        _Capabilities(),
        _Baselines(),
    )

    result = service.capture_build_input(RequestContext("test", run_id="run-1"), config={})

    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "TERMINOLOGY_SOURCE_CHANGED_DURING_CAPTURE"


def test_capture_rejects_unstreamed_source_above_the_production_memory_boundary() -> None:
    service = BuildInputCaptureService(
        _Projects(_state(_registry())),
        _Leases(),
        _Capabilities(),
        _Baselines(),
        max_unstreamed_source_bytes=4,
        max_unstreamed_total_bytes=16,
    )

    result = service.capture_build_input(RequestContext("test", run_id="run-1"), config={})

    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "TERMINOLOGY_STREAMING_REQUIRED"
    assert dict(result.diagnostics[0].details)["recovery"] == "拆分来源或使用支持流式读取的适配器后重试。"


def test_capture_rejects_unstreamed_source_count_before_acquiring_any_lease() -> None:
    registrations = tuple(
        SourceRegistration(
            source_id=f"source-{index}",
            enabled=True,
            format_id=FormatId.PLUGIN_SSE,
            location=f"C:/mods/source-{index}.esp",
            kind=SourceKind.PLUGIN,
            bilingual_capability=BilingualCapability.NONE,
            display_name=f"Source {index}",
        )
        for index in range(3)
    )
    leases = _Leases()
    service = BuildInputCaptureService(
        _Projects(_state(SourceRegistrySnapshot(registrations))),
        leases,
        _Capabilities(),
        _Baselines(),
        max_unstreamed_source_count=2,
    )

    result = service.capture_build_input(RequestContext("test", run_id="run-1"), config={})

    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "TERMINOLOGY_STREAMING_REQUIRED"
    assert dict(result.diagnostics[0].details)["source_count"] == 3
    assert leases.acquired == []


def test_capture_rejects_total_preflight_before_retaining_any_source_bytes() -> None:
    registrations = tuple(
        SourceRegistration(
            source_id=f"source-{index}",
            enabled=True,
            format_id=FormatId.PLUGIN_SSE,
            location=f"C:/mods/source-{index}.esp",
            kind=SourceKind.PLUGIN,
            bilingual_capability=BilingualCapability.NONE,
            display_name=f"Source {index}",
        )
        for index in range(2)
    )

    class _SizedLeases(_Leases):
        @staticmethod
        def source_size(registration):
            del registration
            return 9

    leases = _SizedLeases()
    service = BuildInputCaptureService(
        _Projects(_state(SourceRegistrySnapshot(registrations))),
        leases,
        _Capabilities(),
        _Baselines(),
        max_unstreamed_source_bytes=10,
        max_unstreamed_total_bytes=16,
    )

    result = service.capture_build_input(RequestContext("test", run_id="run-1"), config={})

    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "TERMINOLOGY_STREAMING_REQUIRED"
    assert dict(result.diagnostics[0].details)["captured_bytes"] == 18
    assert leases.acquired == []
