from __future__ import annotations

from types import SimpleNamespace

import pytest

from transbridge.ai_translator.project_terminology_runtime import (
    freeze_project_terminology,
    resolve_project_terminology,
)
from transbridge.ai_translator.translator import AutoTranslator, ProgressCheckpoint
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
    controller = RunController(owner_id="window")
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

    checkpoint = ProgressCheckpoint(
        "plugin",
        ["entry-1"],
        False,
        [],
        {},
        run_id=request.run_id,
        terminology_snapshot=request.spec.terminology_snapshot.to_dict(),
    )
    AutoTranslator._validate_checkpoint_terminology(
        SimpleNamespace(_terminology_snapshot=request.spec.terminology_snapshot),
        checkpoint,
    )


def test_checkpoint_rejects_missing_or_changed_project_terminology_identity() -> None:
    current = SimpleNamespace(
        local_project_id="project-1",
        local_variant_id="variant-1",
        status=EffectiveSnapshotStatus.READY,
        version_id="version-2",
        content_digest="digest-2",
        snapshot_identity="project-1:variant-1:version-2:digest-2",
        captured_at="2026-08-30T00:00:00+00:00",
    )
    checkpoint = ProgressCheckpoint("plugin", None, False, [], {}, run_id="run-1")

    with pytest.raises(ValueError, match="缺少项目术语快照"):
        AutoTranslator._validate_checkpoint_terminology(
            SimpleNamespace(_terminology_snapshot=current),
            checkpoint,
        )
