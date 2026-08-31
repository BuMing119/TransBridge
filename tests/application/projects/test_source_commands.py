from dataclasses import replace

from transbridge.application.contracts import OperationResult, RequestContext
from transbridge.application.io import FormatId
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projects.models import ActiveProject
from transbridge.application.projects.provisioning import PreparedProjectSource, ProjectSourceRequest
from transbridge.application.projects.source_commands import (
    ProjectSourceMutationService,
    source_request_with_initial_entry_states,
)
from transbridge.persistence.v2 import (
    ProjectDto,
    ProjectId,
    ProjectRef,
    SchemaEnvelope,
    SourceBaseline,
    SourceFingerprint,
    VariantAggregate,
    VariantEntryState,
    VariantId,
    VariantRef,
    VariantSnapshot,
)
from transbridge.persistence.v2.baselines import BaselineRegistry


class _Lifecycle:
    def __init__(self, active: ActiveProject) -> None:
        self.active = active

    def commit_active_content(
        self,
        project,
        variant,
        context,
        *,
        expected_project_revision,
        expected_variant_revision,
        before_publish,
    ):
        if self.active.project.envelope.revision != expected_project_revision:
            return OperationResult.failed(ValueError("stale project"), run_id=context.run_id)
        assert self.active.variant is not None
        if self.active.variant.revision != expected_variant_revision:
            return OperationResult.failed(ValueError("stale variant"), run_id=context.run_id)
        before_publish()
        self.active = replace(self.active, project=project, variant=VariantAggregate(variant))
        return OperationResult.completed(self.active.summary(), run_id=context.run_id)


class _Preparer:
    def __init__(self, prepared: PreparedProjectSource) -> None:
        self.prepared = prepared
        self.last_request = None

    def prepare_source(self, request, context, *, role, common_options):
        assert role == "primary"
        assert request.location == self.prepared.to_dict()["location"]
        self.last_request = request
        return self.prepared


def _setup(*, variant_count: int = 1):
    project_ref = ProjectRef(ProjectId("project"))
    variant_ref = VariantRef(VariantId("variant"), project_ref.identity)
    variant_refs = (variant_ref,) + tuple(
        VariantRef(VariantId(f"variant-{index}"), project_ref.identity) for index in range(2, variant_count + 1)
    )
    project = ProjectDto(
        SchemaEnvelope(
            3,
            project_ref.kind,
            project_ref.identity.value,
            0,
            {
                "name": "Project",
                "sources": [],
                "source_relations": [],
                "source_registry_diagnostics": [],
                "variant_ids": [item.identity.value for item in variant_refs],
                "active_variant_id": variant_ref.identity.value,
            },
        )
    )
    aggregate = VariantAggregate(VariantSnapshot(variant_ref, (), ()))
    active = ActiveProject(project, aggregate, variant_ref, 0, 0)
    namespace = SourceNamespace("source:plugin:test")
    baseline = SourceBaseline(
        SourceFingerprint(namespace, "a" * 64),
        (VariantEntryState(EntryKey(namespace, "entry"), "译文", 1),),
    )
    prepared = PreparedProjectSource(
        (
            ("source_id", namespace.value),
            ("enabled", True),
            ("format_id", FormatId.PLUGIN_SSE.value),
            ("location", "D:/fixtures/plugin.esp"),
            ("fingerprint", "a" * 64),
            ("role", "primary"),
        ),
        baseline,
    )
    registry = BaselineRegistry()
    for item in variant_refs:
        registry.register(project_ref, item, (), allow_empty=True)
    lifecycle = _Lifecycle(active)
    service = ProjectSourceMutationService(lifecycle, registry, _Preparer(prepared))
    context = RequestContext("ui", "run", project_ref.identity.value, variant_ref.identity.value)
    return service, lifecycle, registry, project_ref, variant_ref, context


def test_add_and_remove_source_update_project_variant_and_baseline_together() -> None:
    service, lifecycle, registry, project_ref, variant_ref, context = _setup()

    added = service.add_source(
        ProjectSourceRequest("D:/fixtures/plugin.esp", FormatId.PLUGIN_SSE),
        context,
    )

    assert added.is_success
    assert lifecycle.active.project.envelope.revision == 1
    assert lifecycle.active.variant is not None
    added_snapshot = lifecycle.active.variant.snapshot()
    assert added_snapshot.revision == 1
    assert [entry.translation for entry in added_snapshot.entries] == ["译文"]
    assert len(lifecycle.active.project.envelope.data["sources"]) == 1
    assert len(registry.provide(lifecycle.active.project, variant_ref, context)) == 1

    removed = service.remove_source("D:/fixtures/plugin.esp", context)

    assert removed.is_success
    assert lifecycle.active.project.envelope.revision == 2
    assert lifecycle.active.variant is not None
    removed_snapshot = lifecycle.active.variant.snapshot()
    assert removed_snapshot.revision == 2
    assert removed_snapshot.entries == ()
    assert lifecycle.active.project.envelope.data["sources"] == []
    assert registry.provide(lifecycle.active.project, variant_ref, context) == ()


def test_duplicate_source_is_rejected_without_partial_state() -> None:
    service, lifecycle, registry, _project_ref, variant_ref, context = _setup()
    assert service.add_source(ProjectSourceRequest("D:/fixtures/plugin.esp", FormatId.PLUGIN_SSE), context).is_success
    before_project = lifecycle.active.project
    assert lifecycle.active.variant is not None
    before_variant = lifecycle.active.variant.snapshot()

    duplicate = service.add_source(ProjectSourceRequest("D:/fixtures/plugin.esp", FormatId.PLUGIN_SSE), context)

    assert not duplicate.is_success
    assert lifecycle.active.project == before_project
    assert lifecycle.active.variant is not None and lifecycle.active.variant.snapshot() == before_variant
    assert len(registry.provide(lifecycle.active.project, variant_ref, context)) == 1


def test_add_source_applies_initial_import_states_in_the_same_authoritative_commit() -> None:
    service, lifecycle, _registry, _project_ref, _variant_ref, context = _setup()
    request = source_request_with_initial_entry_states(
        ProjectSourceRequest("D:/fixtures/plugin.esp", FormatId.PLUGIN_SSE),
        {"entry": ("导入译文", 3)},
    )

    added = service.add_source(request, context)

    assert added.is_success
    assert lifecycle.active.variant is not None
    entry = lifecycle.active.variant.snapshot().entries[0]
    assert (entry.translation, entry.stage.value, entry.revision.value) == ("导入译文", 3, 1)
    assert service._preparer.last_request.options == ()


def test_multi_variant_source_remove_fails_closed_before_breaking_other_variants() -> None:
    service, lifecycle, registry, project_ref, active_ref, context = _setup(variant_count=2)
    other_ref = VariantRef(VariantId("variant-2"), project_ref.identity)
    assert service.add_source(ProjectSourceRequest("D:/fixtures/plugin.esp", FormatId.PLUGIN_SSE), context).is_success
    before_project = lifecycle.active.project
    assert lifecycle.active.variant is not None
    before_variant = lifecycle.active.variant.snapshot()

    removed = service.remove_source("D:/fixtures/plugin.esp", context)

    assert not removed.is_success
    assert removed.diagnostics[0].code == "PROJECT_SOURCE_MULTI_VARIANT_MIGRATION_REQUIRED"
    assert lifecycle.active.project == before_project
    assert lifecycle.active.variant is not None and lifecycle.active.variant.snapshot() == before_variant
    assert len(registry.provide(lifecycle.active.project, active_ref, context)) == 1
    assert len(registry.provide(lifecycle.active.project, other_ref, context)) == 1
