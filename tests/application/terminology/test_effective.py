from __future__ import annotations

from transbridge.application.terminology.effective import (
    EffectiveSnapshotStatus,
    EffectiveTerminologySnapshot,
    SnapshotEffectiveTerminologyPort,
    TerminologyLookupContext,
)
from transbridge.application.terminology.identity import normalize_original, term_id
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope


def _decision(
    original: str,
    translation: str,
    *,
    scope: TermScope | None = None,
    status: DecisionStatus = DecisionStatus.ADOPTED,
    suppressed: bool = False,
) -> TermDecision:
    resolved_scope = scope or TermScope.project()
    return TermDecision(
        term_id=term_id(
            project_id="project-1",
            variant_id="variant-1",
            scope=resolved_scope,
            original=original,
        ),
        project_id="project-1",
        variant_id="variant-1",
        original=original,
        normalized_original=normalize_original(original),
        translation=translation,
        scope=resolved_scope,
        status=status,
        suppressed=suppressed,
    )


class _Snapshots:
    def __init__(self, snapshot: EffectiveTerminologySnapshot) -> None:
        self.value = snapshot

    def snapshot(self, local_project_id, local_variant_id, version_id=None):
        del local_project_id, local_variant_id, version_id
        return self.value


def _ready(*decisions: TermDecision) -> EffectiveTerminologySnapshot:
    return EffectiveTerminologySnapshot(
        "project-1",
        "variant-1",
        EffectiveSnapshotStatus.READY,
        version_id="version-1",
        content_digest="content-1",
        decisions=decisions,
    )


def test_resolve_only_adopted_or_manual_confirmed_with_plugin_then_global_precedence():
    global_entry = _decision("Sword", "剑")
    plugin_entry = _decision(
        "Sword",
        "长剑",
        scope=TermScope.plugin("plugin-a.esp"),
        status=DecisionStatus.MANUAL_CONFIRMED,
    )
    review = _decision("Shield", "盾", status=DecisionStatus.REVIEW_REQUIRED)
    unresolved = _decision("Bow", "弓", status=DecisionStatus.UNRESOLVED)
    port = SnapshotEffectiveTerminologyPort(_Snapshots(_ready(global_entry, plugin_entry, review, unresolved)))

    plugin = port.resolve("sword", TerminologyLookupContext("project-1", "variant-1", "plugin-a.esp"))
    other_plugin = port.resolve("Sword", TerminologyLookupContext("project-1", "variant-1", "plugin-b.esp"))

    assert plugin.decision == plugin_entry
    assert other_plugin.decision == global_entry
    assert port.resolve("Shield", TerminologyLookupContext("project-1", "variant-1")).decision is None
    assert port.resolve("Bow", TerminologyLookupContext("project-1", "variant-1")).decision is None


def test_suppression_blocks_legacy_in_its_applicable_fallback_scope():
    global_suppression = _decision("Sword", "剑", suppressed=True)
    port = SnapshotEffectiveTerminologyPort(_Snapshots(_ready(global_suppression)))

    result = port.resolve("Sword", TerminologyLookupContext("project-1", "variant-1", "plugin-a.esp"))

    assert result.decision is None
    assert result.blocks_legacy_fallback


def test_unresolved_and_review_required_shadow_lower_scope_and_legacy_fallback():
    global_entry = _decision("Sword", "项目剑")
    plugin_review = _decision(
        "Sword",
        "待定剑",
        scope=TermScope.plugin("plugin-a.esp"),
        status=DecisionStatus.REVIEW_REQUIRED,
    )
    unresolved = _decision("Shield", "待定盾", status=DecisionStatus.UNRESOLVED)
    port = SnapshotEffectiveTerminologyPort(_Snapshots(_ready(global_entry, plugin_review, unresolved)))

    plugin = port.resolve("Sword", TerminologyLookupContext("project-1", "variant-1", "plugin-a.esp"))
    other_plugin = port.resolve("Sword", TerminologyLookupContext("project-1", "variant-1", "plugin-b.esp"))
    unresolved_result = port.resolve("Shield", TerminologyLookupContext("project-1", "variant-1"))
    unmatched = port.resolve("Bow", TerminologyLookupContext("project-1", "variant-1"))

    assert plugin.decision is None and plugin.blocks_legacy_fallback
    assert other_plugin.decision == global_entry and not other_plugin.blocks_legacy_fallback
    assert unresolved_result.decision is None and unresolved_result.blocks_legacy_fallback
    assert unmatched.decision is None and not unmatched.blocks_legacy_fallback


def test_no_version_and_corrupt_are_distinct_read_only_results():
    no_version = EffectiveTerminologySnapshot(
        "project-1",
        "variant-1",
        EffectiveSnapshotStatus.NO_PROJECT_VERSION,
    )
    missing = SnapshotEffectiveTerminologyPort(_Snapshots(no_version)).resolve(
        "Sword", TerminologyLookupContext("project-1", "variant-1")
    )
    assert not missing.blocks_legacy_fallback

    corrupt = EffectiveTerminologySnapshot(
        "project-1",
        "variant-1",
        EffectiveSnapshotStatus.CORRUPT,
        diagnostics=("VERSION_DIGEST_MISMATCH",),
    )
    damaged = SnapshotEffectiveTerminologyPort(_Snapshots(corrupt)).resolve(
        "Sword", TerminologyLookupContext("project-1", "variant-1")
    )
    assert not damaged.blocks_legacy_fallback
    assert damaged.snapshot.diagnostics == ("VERSION_DIGEST_MISMATCH",)
