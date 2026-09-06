from __future__ import annotations

from datetime import UTC, datetime

from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.application.terminology_profiles import (
    InMemoryTerminologyProfileRepository,
    ProfileTermMapping,
    TerminologyProfileContent,
    TerminologyProfileService,
    TerminologyProfileWriteProjectionSource,
)
from transbridge.converter.translation_entry import TranslationEntry


def _service() -> TerminologyProfileService:
    ids = iter(("profile-a", "profile-b"))
    return TerminologyProfileService(
        InMemoryTerminologyProfileRepository(),
        now=lambda: datetime(2026, 9, 6, tzinfo=UTC),
        new_id=lambda: next(ids),
    )


def _base_snapshot() -> EffectiveTerminologySnapshot:
    return EffectiveTerminologySnapshot(
        "project-1",
        "variant-1",
        EffectiveSnapshotStatus.READY,
        version_id="base-1",
        content_digest="1" * 64,
        decisions=(
            TermDecision(
                "term-whiterun",
                "project-1",
                "variant-1",
                "Whiterun",
                "whiterun",
                "雪漫",
                TermScope.project(),
                DecisionStatus.ADOPTED,
            ),
        ),
    )


def _published(service: TerminologyProfileService, name: str, translation: str) -> str:
    profile = service.create("project-1", name)
    draft = service.save_draft(
        profile.profile_id,
        TerminologyProfileContent(mappings=(ProfileTermMapping("Whiterun", translation, "雪漫"),)),
        expected_revision=0,
    )
    service.publish(profile.profile_id, expected_draft_revision=draft.draft_revision)
    return profile.profile_id


def test_frozen_write_projection_is_stable_after_current_selection_changes() -> None:
    service = _service()
    profile_a = _published(service, "A", "白漫")
    profile_b = _published(service, "B", "雪漫城")
    service.select("project-1", "variant-1", profile_a)
    source = TerminologyProfileWriteProjectionSource(
        lambda _project_id: service,
        base_snapshot_for=lambda _project_id, _variant_id: _base_snapshot(),
    )
    frozen_a = source.freeze("project-1", "variant-1")
    common = TranslationEntry("e1", "e1", "Visit Whiterun", "前往雪漫", 1, None)

    service.select("project-1", "variant-1", profile_b)
    projected_a, _ = frozen_a.project_entries((common,))
    projected_b, _ = source.freeze("project-1", "variant-1").project_entries((common,))

    assert projected_a[0].translation == "前往白漫"
    assert projected_b[0].translation == "前往雪漫城"
    assert common.translation == "前往雪漫"
    assert frozen_a.metadata[0] == ("terminology_profile_id", profile_a)
    assert dict(frozen_a.metadata)["base_terminology_version"] == "base-1"


def test_unselected_write_projection_returns_original_entries() -> None:
    service = _service()
    entry = TranslationEntry("e1", "e1", "Hello", "你好", 1, None)

    projected, diagnostics = (
        TerminologyProfileWriteProjectionSource(lambda _project_id: service)
        .freeze("project-1", "variant-1")
        .project_entries((entry,))
    )

    assert projected == (entry,)
    assert diagnostics == ()


def test_missing_mapping_keeps_entire_entry_common_and_reports_it() -> None:
    service = _service()
    profile_id = _published(service, "A", "白漫")
    service.select("project-1", "variant-1", profile_id)
    base = _base_snapshot()
    extra = TermDecision(
        "term-solitude",
        "project-1",
        "variant-1",
        "Solitude",
        "solitude",
        "独孤城",
        TermScope.project(),
        DecisionStatus.ADOPTED,
    )
    base = EffectiveTerminologySnapshot(
        base.local_project_id,
        base.local_variant_id,
        base.status,
        base.version_id,
        base.content_digest,
        base.decisions + (extra,),
    )
    frozen = TerminologyProfileWriteProjectionSource(
        lambda _project_id: service,
        base_snapshot_for=lambda _project_id, _variant_id: base,
    ).freeze("project-1", "variant-1")
    entry = TranslationEntry("e1", "e1", "Whiterun and Solitude", "雪漫与独孤城", 1, None)

    projected, diagnostics = frozen.project_entries((entry,))

    assert projected[0].translation == entry.translation
    assert {item.code for item in diagnostics} == {"profile_mapping_missing"}
