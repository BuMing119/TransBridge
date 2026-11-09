from __future__ import annotations

from types import SimpleNamespace

from tests.ui.tools.ai_runtime_support import ai_task_runtime
from transbridge.ai_translator.project_terminology_runtime import (
    freeze_project_terminology,
    resolve_project_terminology,
)
from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.identity import normalize_original, term_id
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.ui.tools.ai_translator.run_controller import RunController
from transbridge.ui.tools.ai_translator.task_adapter import runtime_job_spec


def test_resolve_project_terminology_uses_injected_project_variant_factory() -> None:
    adapter = object()
    calls: list[tuple[str, str]] = []

    class Factory:
        def effective_adapter(self, project_id: str, variant_id: str):
            calls.append((project_id, variant_id))
            return adapter

    binding = resolve_project_terminology(
        SimpleNamespace(
            active_version_identity=("project-1", "variant-2"),
            effective_terminology_factory=Factory(),
        )
    )

    assert calls == [("project-1", "variant-2")]
    assert binding.translator_kwargs() == {
        "effective_terminology": adapter,
        "terminology_context": binding.context,
    }
    assert binding.term_database_kwargs() == {
        "effective_loader": adapter,
        "terminology_context": binding.context,
    }
    assert binding.context is not None
    assert (binding.context.local_project_id, binding.context.local_variant_id) == ("project-1", "variant-2")


def test_resolve_project_terminology_supports_callable_factory() -> None:
    adapter = object()
    binding = resolve_project_terminology(
        SimpleNamespace(
            active_version_identity=("project-1", "variant-1"),
            effective_terminology_factory=lambda _project_id, _variant_id: adapter,
        )
    )

    assert binding.adapter is adapter


def test_resolve_project_terminology_preserves_legacy_when_identity_or_storage_is_unavailable() -> None:
    missing_identity = resolve_project_terminology(
        SimpleNamespace(active_version_identity=None, effective_terminology_factory=lambda *_args: object())
    )
    unavailable = resolve_project_terminology(
        SimpleNamespace(
            active_version_identity=("project-1", "variant-1"),
            effective_terminology_factory=lambda *_args: (_ for _ in ()).throw(OSError("read-only store failed")),
        )
    )

    assert missing_identity.translator_kwargs() == {}
    assert missing_identity.term_database_kwargs() == {}
    assert unavailable.translator_kwargs() == {}
    assert unavailable.term_database_kwargs() == {}


def test_freeze_project_terminology_binds_explicit_version_and_ignores_later_current() -> None:
    scope = TermScope.project()

    def decision(translation: str) -> TermDecision:
        return TermDecision(
            term_id(project_id="project-1", variant_id="variant-1", scope=scope, original="Sword"),
            "project-1",
            "variant-1",
            "Sword",
            normalize_original("Sword"),
            translation,
            scope=scope,
            status=DecisionStatus.ADOPTED,
        )

    snapshots = [
        EffectiveTerminologySnapshot(
            "project-1",
            "variant-1",
            EffectiveSnapshotStatus.READY,
            version_id="version-1",
            content_digest="digest-1",
            decisions=(decision("旧剑"),),
        ),
        EffectiveTerminologySnapshot(
            "project-1",
            "variant-1",
            EffectiveSnapshotStatus.READY,
            version_id="version-2",
            content_digest="digest-2",
            decisions=(decision("新剑"),),
        ),
    ]
    calls = 0

    class Adapter:
        def effective_snapshot(self, _context):
            nonlocal calls
            value = snapshots[min(calls, 1)]
            calls += 1
            return value

    owner = SimpleNamespace(
        active_version_identity=("project-1", "variant-1"),
        effective_terminology_factory=lambda *_args: Adapter(),
    )

    binding = freeze_project_terminology(owner)
    loaded = binding.adapter.load(binding.context, ())
    loaded_again = binding.adapter.load(binding.context, ())

    assert calls == 1
    assert binding.snapshot_ref.version_id == "version-1"
    assert binding.context.version_id == "version-1"
    assert loaded.entries[0].translation == "旧剑"
    assert loaded_again.entries[0].translation == "旧剑"


def test_run_controller_archives_and_reuses_the_same_frozen_binding() -> None:
    scope = TermScope.project()
    project_decision = TermDecision(
        term_id(project_id="project-1", variant_id="variant-1", scope=scope, original="Sword"),
        "project-1",
        "variant-1",
        "Sword",
        normalize_original("Sword"),
        "剑",
        scope=scope,
        status=DecisionStatus.ADOPTED,
    )
    snapshot = EffectiveTerminologySnapshot(
        "project-1",
        "variant-1",
        EffectiveSnapshotStatus.READY,
        version_id="version-1",
        content_digest="digest-1",
        decisions=(project_decision,),
    )
    calls = 0

    class Adapter:
        def effective_snapshot(self, _context):
            nonlocal calls
            calls += 1
            return snapshot

    owner = SimpleNamespace(
        active_version_identity=("project-1", "variant-1"),
        effective_terminology_factory=lambda *_args: Adapter(),
    )
    controller = RunController(task_runtime=ai_task_runtime(), owner_id="window")
    request = controller.begin(
        "translate",
        SimpleNamespace(max_concurrent=1),
        [SimpleNamespace(id="entry-1")],
        esp_path="plugin.esp",
        terminology_owner=owner,
    )

    assert calls == 1
    assert request.spec.terminology_snapshot is request.terminology_binding.snapshot_ref
    assert request.terminology_binding.context.version_id == "version-1"
    metadata = dict(runtime_job_spec(request.spec).metadata)
    assert metadata["terminology_version_id"] == "version-1"
    assert metadata["terminology_content_digest"] == "digest-1"


def test_production_gate_freezes_actual_scheme_without_a_variant_base_version(tmp_path):
    from tests.integration.bootstrap.test_terminology_task_wiring import _create_project_context
    from transbridge.application.terminology_profiles import ProfileTermMapping, TerminologyProfileContent
    from transbridge.bootstrap import build_runtime

    app = build_runtime(settings={"persistence_v2_root": tmp_path})
    try:
        context = _create_project_context(app, "AI terminology binding")
        project_id = context.project_id
        composition = app.use_cases.resolve("terminology_ui_services_factory")
        commands = app.use_cases.resolve("gui_project_commands")
        profiles = composition.profile_service_for(project_id)
        profile = profiles.create_with_content(
            project_id, "B", TerminologyProfileContent((ProfileTermMapping("Sword", "B之剑"),))
        )
        published = profiles.publish(profile.profile_id, expected_draft_revision=0)
        saved = commands.save_profile_variant("B", context, target_profile=published, source_profile=None)
        assert saved.is_success, saved.diagnostics
        variant_id = saved.value["id"]
        assert composition.repositories.for_project(project_id).effective_version(project_id, variant_id) is None
        owner = SimpleNamespace(
            active_version_identity=(project_id, variant_id), effective_terminology_factory=composition
        )
        binding = freeze_project_terminology(owner)
        assert binding.snapshot_ref.status is EffectiveSnapshotStatus.READY
        assert binding.snapshot_ref.version_id.startswith("profiled:v2:")
        loaded = binding.adapter.load(binding.context, ())
        assert [(entry.term, entry.translation) for entry in loaded.entries] == [("Sword", "B之剑")]
    finally:
        assert app.close().is_success
