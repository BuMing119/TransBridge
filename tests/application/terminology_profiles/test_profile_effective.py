from __future__ import annotations

from datetime import UTC, datetime

from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.application.terminology_profiles import (
    InMemoryTerminologyProfileRepository,
    ProfiledEffectiveTerminologySnapshotPort,
    ProfileTermMapping,
    TerminologyProfileContent,
    TerminologyProfileService,
    decode_profiled_version_id,
    is_profiled_version_id,
)


class _BaseSnapshots:
    def __init__(self) -> None:
        self.current = "base-2"
        self.snapshots = {
            "base-1": _snapshot("base-1", "1" * 64),
            "base-2": _snapshot("base-2", "2" * 64),
        }

    def snapshot(self, project_id: str, variant_id: str, version_id: str | None = None) -> EffectiveTerminologySnapshot:
        assert (project_id, variant_id) == ("project-1", "variant-1")
        return self.snapshots[version_id or self.current]


def _snapshot(version: str, digest: str) -> EffectiveTerminologySnapshot:
    return EffectiveTerminologySnapshot(
        "project-1",
        "variant-1",
        EffectiveSnapshotStatus.READY,
        version_id=version,
        content_digest=digest,
        decisions=(
            TermDecision(
                "term-solitude",
                "project-1",
                "variant-1",
                "Solitude",
                "solitude",
                "独孤城",
                TermScope.project(),
                DecisionStatus.ADOPTED,
            ),
            TermDecision(
                "term-whiterun",
                "project-1",
                "variant-1",
                "Whiterun",
                "whiterun",
                "雪漫城",
                TermScope.project(),
                DecisionStatus.ADOPTED,
            ),
        ),
    )


def _profile_service() -> TerminologyProfileService:
    return TerminologyProfileService(
        InMemoryTerminologyProfileRepository(),
        now=lambda: datetime(2026, 9, 6, tzinfo=UTC),
        new_id=lambda: "profile-a",
    )


def test_unselected_profile_preserves_legacy_snapshot_exactly() -> None:
    base = _BaseSnapshots()
    profiled = ProfiledEffectiveTerminologySnapshotPort(base, _profile_service())

    assert profiled.snapshot("project-1", "variant-1") is base.snapshots["base-2"]


def test_selected_profile_overlays_mapped_terms_and_shadows_missing_terms() -> None:
    profiles = _profile_service()
    profile = profiles.create("project-1", "大学汉化")
    draft = profiles.save_draft(
        profile.profile_id,
        TerminologyProfileContent(mappings=(ProfileTermMapping("Whiterun", "白漫城", "雪漫城"),)),
        expected_revision=0,
    )
    profiles.publish(profile.profile_id, expected_draft_revision=draft.draft_revision)
    profiles.select("project-1", "variant-1", profile.profile_id)

    snapshot = ProfiledEffectiveTerminologySnapshotPort(_BaseSnapshots(), profiles).snapshot("project-1", "variant-1")
    decisions = {item.original: item for item in snapshot.decisions}

    assert is_profiled_version_id(snapshot.version_id)
    assert decisions["Whiterun"].translation == "白漫城"
    assert decisions["Whiterun"].is_effective
    assert decisions["Solitude"].suppressed
    assert not decisions["Solitude"].is_effective
    assert "1 unmapped" in snapshot.diagnostics[-1]


def test_profiled_version_restores_exact_base_and_profile_revision_after_selection_changes() -> None:
    base = _BaseSnapshots()
    profiles = _profile_service()
    profile = profiles.create("project-1", "A")
    draft = profiles.save_draft(
        profile.profile_id,
        TerminologyProfileContent(mappings=(ProfileTermMapping("Whiterun", "白漫", "雪漫城"),)),
        expected_revision=0,
    )
    profiles.publish(profile.profile_id, expected_draft_revision=draft.draft_revision)
    profiles.select("project-1", "variant-1", profile.profile_id)
    source = ProfiledEffectiveTerminologySnapshotPort(base, profiles)
    frozen = source.snapshot("project-1", "variant-1")
    descriptor = decode_profiled_version_id(frozen.version_id or "")

    second_draft = profiles.save_draft(
        profile.profile_id,
        TerminologyProfileContent(mappings=(ProfileTermMapping("Whiterun", "新白漫", "雪漫城"),)),
        expected_revision=1,
    )
    profiles.publish(profile.profile_id, expected_draft_revision=second_draft.draft_revision)
    assert profiles.selected_revision("project-1", "variant-1").revision == 2
    base.current = "base-1"
    restored = source.snapshot("project-1", "variant-1", frozen.version_id)

    assert descriptor["base_version_id"] == "base-2"
    assert restored == frozen
    assert source.snapshot("project-1", "variant-1", "base-1") is base.snapshots["base-1"]
