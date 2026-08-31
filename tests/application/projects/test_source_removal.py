from copy import deepcopy
from dataclasses import replace

import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projects.lifecycle import ProjectLifecycleService
from transbridge.application.projects.models import ActiveProject
from transbridge.application.projects.source_commands import ProjectSourceMutationService
from transbridge.application.projects.source_registry import migrate_legacy_source_registry
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


def _source(name, *, role="primary", namespace="source:plugin:paired", fingerprint="a" * 64):
    return {
        "location": f"D:/fixtures/{name}.esp",
        "source_id": namespace,
        "role": role,
        "format_id": FormatId.PLUGIN_SSE.value,
        "fingerprint": fingerprint,
    }


def _baseline(source):
    namespace = SourceNamespace(source["source_id"])
    return SourceBaseline(
        SourceFingerprint(namespace, source["fingerprint"]),
        (VariantEntryState(EntryKey(namespace, "entry"), "已导入且编辑的译文", 1),),
    )


def _setup(sources, baselines, *, variant_count=1):
    project_ref = ProjectRef(ProjectId("project"))
    refs = tuple(VariantRef(VariantId(f"variant-{i}"), project_ref.identity) for i in range(variant_count))
    registry = migrate_legacy_source_registry(project_ref.identity.value, sources)
    project = ProjectDto(
        SchemaEnvelope(
            3,
            project_ref.kind,
            project_ref.identity.value,
            0,
            {
                "name": "Paired plugin",
                **registry.to_project_data(),
                "variant_ids": [ref.identity.value for ref in refs],
                "active_variant_id": refs[0].identity.value,
            },
        )
    )
    snapshot = VariantSnapshot(
        refs[0],
        tuple(baseline.fingerprint for baseline in baselines),
        tuple(entry for baseline in baselines for entry in baseline.entries),
    )
    active = ActiveProject(project, VariantAggregate(snapshot), refs[0], 0, 0)
    lifecycle = ProjectLifecycleService(None, None, active=active)
    baseline_registry = BaselineRegistry()
    for ref in refs:
        baseline_registry.register(project_ref, ref, baselines)
    service = ProjectSourceMutationService(lifecycle, baseline_registry, None)
    context = RequestContext("gui", "remove-paired-source", project_ref.identity.value, refs[0].identity.value)
    return service, lifecycle, baseline_registry, context


@pytest.mark.parametrize("legacy", [False, True])
def test_remove_primary_also_removes_folded_translation_registration(legacy):
    primary = _source("original")
    translated = _source("translated", role="migration", fingerprint="b" * 64)
    service, lifecycle, registry, context = _setup((primary, translated), (_baseline(primary),))
    if legacy:
        data = dict(lifecycle.active.project.envelope.data)
        data["sources"] = [primary, translated]
        data.pop("source_relations")
        lifecycle._active = replace(
            lifecycle.active,
            project=ProjectDto(replace(lifecycle.active.project.envelope, data=data)),
        )

    removed = service.remove_source(primary["location"], context)

    assert removed.is_success, removed.diagnostics
    active = lifecycle.active
    assert active.dirty
    assert active.project.envelope.data["sources"] == []
    assert active.project.envelope.data["source_relations"] == []
    assert active.variant.snapshot().entries == ()
    assert active.variant.snapshot().source_fingerprints == ()
    assert (active.project.envelope.revision, active.variant.revision) == (1, 1)
    assert registry.provide(active.project, active.formal_variant_ref, context) == ()


@pytest.mark.parametrize("same_fingerprint", [False, True])
def test_remove_folded_import_preserves_primary_and_existing_translations(same_fingerprint):
    primary = _source("original")
    translated = _source("translated", role="migration", fingerprint=("a" if same_fingerprint else "b") * 64)
    baseline = _baseline(primary)
    service, lifecycle, registry, context = _setup((primary, translated), (baseline,))
    before = lifecycle.active.variant.snapshot()

    removed = service.remove_source(translated["location"], context)

    assert removed.is_success, removed.diagnostics
    active = lifecycle.active
    assert len(active.project.envelope.data["sources"]) == 1
    assert active.project.envelope.data["sources"][0]["legacy"]["role"] == "primary"
    assert active.project.envelope.data["source_relations"] == []
    assert active.variant.snapshot() == before
    assert active.dirty
    assert registry.provide(active.project, active.formal_variant_ref, context) == (baseline,)


def test_remove_pair_preserves_another_plugin_even_with_the_same_file_digest():
    primary = _source("original")
    translated = _source("translated", role="migration", fingerprint="b" * 64)
    other = _source("unrelated", namespace="source:plugin:other")
    other_baseline = _baseline(other)
    service, lifecycle, registry, context = _setup((primary, translated, other), (_baseline(primary), other_baseline))

    removed = service.remove_source(primary["location"], context)

    assert removed.is_success, removed.diagnostics
    active = lifecycle.active
    assert len(active.project.envelope.data["sources"]) == 1
    assert active.project.envelope.data["sources"][0]["legacy"]["source_id"] == other["source_id"]
    assert active.variant.snapshot().entries == other_baseline.entries
    assert active.variant.snapshot().source_fingerprints == (other_baseline.fingerprint,)
    assert registry.provide(active.project, active.formal_variant_ref, context) == (other_baseline,)


def test_distinct_translation_source_is_not_removed_as_a_folded_import():
    primary = _source("original")
    translated = _source("different", role="migration", namespace="source:plugin:other", fingerprint="b" * 64)
    other_baseline = _baseline(translated)
    service, lifecycle, registry, context = _setup((primary, translated), (_baseline(primary), other_baseline))

    removed = service.remove_source(primary["location"], context)

    assert removed.is_success, removed.diagnostics
    active = lifecycle.active
    assert len(active.project.envelope.data["sources"]) == 1
    assert active.variant.snapshot().entries == other_baseline.entries
    assert registry.provide(active.project, active.formal_variant_ref, context) == (other_baseline,)


def test_ambiguous_pair_is_rejected_without_changing_any_state():
    primary = _source("original")
    imports = tuple(_source(name, role="migration", fingerprint="b" * 64) for name in ("one", "two"))
    baseline = _baseline(primary)
    service, lifecycle, registry, context = _setup((primary, *imports), (baseline,))
    before = lifecycle.active
    before_data = deepcopy(before.project.envelope.data)

    removed = service.remove_source(primary["location"], context)

    assert not removed.is_success
    assert removed.diagnostics[0].code == "PROJECT_SOURCE_DEPENDENCY_AMBIGUOUS"
    assert lifecycle.active is before
    assert before.project.envelope.data == before_data
    assert registry.provide(before.project, before.formal_variant_ref, context) == (baseline,)


def test_multi_variant_pair_removal_keeps_all_variants_usable():
    primary = _source("original")
    translated = _source("translated", role="migration", fingerprint="b" * 64)
    baseline = _baseline(primary)
    service, lifecycle, registry, context = _setup((primary, translated), (baseline,), variant_count=2)
    before = lifecycle.active

    removed = service.remove_source(primary["location"], context)

    assert not removed.is_success
    assert removed.diagnostics[0].code == "PROJECT_SOURCE_MULTI_VARIANT_MIGRATION_REQUIRED"
    assert lifecycle.active is before
    for variant_id in before.project.envelope.data["variant_ids"]:
        ref = VariantRef(VariantId(variant_id), before.project_ref.identity)
        assert registry.provide(before.project, ref, context) == (baseline,)


def test_wrong_variant_context_cannot_partially_remove_a_pair():
    primary = _source("original")
    translated = _source("translated", role="migration", fingerprint="b" * 64)
    baseline = _baseline(primary)
    service, lifecycle, registry, context = _setup((primary, translated), (baseline,))
    before = lifecycle.active

    removed = service.remove_source(primary["location"], replace(context, variant_id="other-variant"))

    assert not removed.is_success
    assert removed.diagnostics[0].code == "VARIANT_CONTEXT_MISMATCH"
    assert lifecycle.active is before
    assert registry.provide(before.project, before.formal_variant_ref, context) == (baseline,)


def test_baseline_publish_failure_retains_state_and_records_original_exception(monkeypatch, caplog):
    primary = _source("original")
    translated = _source("translated", role="migration", fingerprint="b" * 64)
    baseline = _baseline(primary)
    service, lifecycle, registry, context = _setup((primary, translated), (baseline,))
    before = lifecycle.active

    def fail(*args, **kwargs):
        raise RuntimeError("injected baseline failure")

    monkeypatch.setattr(registry, "replace_many", fail)
    removed = service.remove_source(primary["location"], context)

    assert not removed.is_success
    assert removed.diagnostics[0].code == "ACTIVE_CONTENT_CHANGE_FAILED"
    assert lifecycle.active is before
    assert registry.provide(before.project, before.formal_variant_ref, context) == (baseline,)
    assert "injected baseline failure" in caplog.text
    assert any(record.exc_info for record in caplog.records)
