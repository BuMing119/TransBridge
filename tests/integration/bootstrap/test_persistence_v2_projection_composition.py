from __future__ import annotations

from pathlib import Path

from transbridge.application.contracts import RequestContext
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projects import DirtyDecision
from transbridge.bootstrap import build_runtime
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.v2 import (
    ProjectDto,
    ProjectId,
    ProjectRef,
    SchemaEnvelope,
    SourceBaseline,
    SourceFingerprint,
    VariantEntryState,
    VariantId,
    VariantRef,
    VariantSnapshot,
)


class _Ids:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"id-{self.value}"


def _records(services):
    project_ref = ProjectRef(ProjectId("project-a"))
    variant_ref = VariantRef(VariantId("variant-a"), project_ref.identity)
    namespace = SourceNamespace("source-a")
    entry = VariantEntryState(EntryKey(namespace, "entry-a"), "old", 1)
    fingerprint = SourceFingerprint(namespace, "a" * 64)
    project = ProjectDto(
        SchemaEnvelope(
            2,
            project_ref.kind,
            project_ref.identity.value,
            0,
            {
                "name": "Project A",
                "sources": [],
                "variant_ids": [variant_ref.identity.value],
                "active_variant_id": variant_ref.identity.value,
            },
        )
    )
    variant = VariantSnapshot(variant_ref, (fingerprint,), (entry,), 0)
    services.projects.save(project_ref, project)
    services.variants.save(variant_ref, variant.to_dto())
    return project_ref, variant_ref, SourceBaseline(fingerprint, (entry,))


def test_legacy_mapping_and_baseline_are_both_required_before_active_changes(tmp_path: Path) -> None:
    ids = _Ids()
    services = build_persistence_v2_services(
        tmp_path / "projects-v2",
        id_factory=ids,
        timestamp_factory=lambda: "2026-08-18T00:00:00+00:00",
    )
    project_ref, variant_ref, baseline = _records(services)
    context = RequestContext("gui", run_id="run")

    unmapped = services.gui_project_commands.switch_legacy("legacy/project.json", "Main", context)
    services.legacy_identities.register("legacy/project.json", "Main", project_ref, variant_ref)
    missing_baseline = services.gui_project_commands.switch_legacy("legacy/project.json", "Main", context)
    services.baselines.register(project_ref, variant_ref, (baseline,))
    activated = services.gui_project_commands.switch_legacy("legacy/project.json", "Main", context)

    assert unmapped.diagnostics[0].code == "LEGACY_ID_MAPPING_REQUIRED"
    assert missing_baseline.diagnostics[0].code == "LEGACY_SOURCE_BASELINE_REQUIRED"
    assert activated.is_success
    assert services.project_lifecycle.active is not None
    projection = services.project_projection.snapshot()
    assert projection is not None
    assert projection.to_dict()["values"]["variant_id"] == "variant-a"


def test_variant_gui_commands_make_dirty_from_revision_then_save_clears_it(tmp_path: Path) -> None:
    ids = _Ids()
    services = build_persistence_v2_services(
        tmp_path / "projects-v2",
        id_factory=ids,
        timestamp_factory=lambda: "2026-08-18T00:00:00+00:00",
    )
    project_ref, variant_ref, baseline = _records(services)
    services.baselines.register(project_ref, variant_ref, (baseline,))
    context = RequestContext("gui", run_id="run")
    assert services.gui_project_commands.switch_v2(
        project_ref,
        variant_ref,
        context,
        dirty_decision=DirtyDecision.SAVE,
    ).is_success

    changed = services.gui_project_commands.update_entry(
        "entry-a",
        context,
        translation="new",
    )
    dirty = services.project_projection.snapshot()
    saved = services.gui_project_commands.save(context)
    clean = services.project_projection.snapshot()

    assert changed.is_success
    assert dirty is not None and dirty.dirty
    assert saved.is_success
    assert clean is not None and not clean.dirty
    assert clean.to_dict()["values"]["entries"][0]["translation"] == "new"


def test_session_gui_commands_publish_only_committed_full_conversation(tmp_path: Path) -> None:
    ids = _Ids()
    services = build_persistence_v2_services(
        tmp_path / "projects-v2",
        id_factory=ids,
        timestamp_factory=lambda: "2026-08-18T00:00:00+00:00",
    )
    context = RequestContext("gui", run_id="run")
    created = services.gui_session_commands.create_and_activate("Session A", context)
    session_id = created.value["session_id"]
    saved = services.gui_session_commands.save_conversation(
        services.session_lifecycle.active.aggregate.ref,
        [{"role": "user", "content": "visible"}],
        [{"role": "user", "content": "backend"}],
        context,
    )
    projection = services.session_projection.snapshot()

    assert created.is_success
    assert saved.is_success
    assert projection is not None and not projection.dirty
    assert projection.to_dict()["values"]["session_id"] == session_id
    assert projection.to_dict()["values"]["backend_history"][0]["content"] == "backend"

    services.close()
    restarted = build_persistence_v2_services(
        tmp_path / "projects-v2",
        id_factory=_Ids(),
        timestamp_factory=lambda: "2026-08-18T00:01:00+00:00",
    )
    catalog = restarted.gui_session_commands.list_sessions()
    restored = restarted.gui_session_commands.switch(
        services.session_lifecycle.active.aggregate.ref,
        context,
    )

    assert catalog[0]["session_id"] == session_id
    assert catalog[0]["message_count"] == 1
    assert restored.is_success
    restored_projection = restarted.session_projection.snapshot()
    assert restored_projection.to_dict()["values"]["backend_history"][0]["content"] == "backend"


def test_runtime_registers_real_v2_services_and_releases_projection_subscriptions(tmp_path: Path) -> None:
    root = tmp_path / "projects-v2"
    runtime = build_runtime({"persistence_v2_root": str(root)})
    services = runtime.use_cases.resolve("persistence_v2")
    subscription = services.project_projection.subscribe(lambda snapshot: None)

    result = runtime.close()

    assert services.root == str(root.resolve())
    assert result.is_success
    assert services.project_projection.listener_count == 0
    assert subscription.closed
