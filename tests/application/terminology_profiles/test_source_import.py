from __future__ import annotations

from datetime import UTC, datetime

import pytest

from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.application.terminology_profiles import (
    InMemoryTerminologyProfileRepository,
    TerminologyProfileImportConflictKind,
    TerminologyProfileImportError,
    TerminologyProfileImportService,
    TerminologyProfileService,
    TerminologySourceEntry,
    TerminologySourceSnapshot,
)


def _decision(
    term_id: str,
    original: str,
    translation: str,
    scope: TermScope | None = None,
    *,
    status: DecisionStatus = DecisionStatus.ADOPTED,
) -> TermDecision:
    return TermDecision(
        term_id,
        "project-1",
        "variant-1",
        original,
        original.casefold(),
        translation,
        scope or TermScope.project(),
        status,
    )


def _snapshot(*decisions: TermDecision) -> EffectiveTerminologySnapshot:
    return EffectiveTerminologySnapshot(
        "project-1",
        "variant-1",
        EffectiveSnapshotStatus.READY,
        version_id="base-v1",
        content_digest="a" * 64,
        decisions=decisions,
    )


def _source(*entries: tuple[str, str]) -> TerminologySourceSnapshot:
    return TerminologySourceSnapshot.capture(
        "json",
        "本地 JSON",
        (TerminologySourceEntry(original, translation) for original, translation in entries),
    )


def _importer() -> tuple[TerminologyProfileImportService, TerminologyProfileService]:
    ids = iter(("profile-1", "profile-2"))
    profiles = TerminologyProfileService(
        InMemoryTerminologyProfileRepository(),
        now=lambda: datetime(2026, 9, 6, tzinfo=UTC),
        new_id=lambda: next(ids),
    )
    return TerminologyProfileImportService(profiles), profiles


def test_source_capture_has_order_independent_digest_and_immutable_entries() -> None:
    first = _source(("Whiterun", "白漫城"), ("Solitude", "独孤城"))
    second = _source(("Solitude", "独孤城"), ("Whiterun", "白漫城"))

    assert first.source_digest == second.source_digest
    assert isinstance(first.entries, tuple)


def test_preview_normalizes_only_for_comparison_and_preserves_source_translation_text() -> None:
    importer, _ = _importer()

    preview = importer.preview(
        "project-1",
        "variant-1",
        _snapshot(_decision("term-a", "Letter A", "字母A")),
        _source(("Letter A", "字母Ａ")),
    )

    assert preview.content.mappings[0].translation == "字母Ａ"


def test_preview_uses_effective_base_as_full_skeleton_and_deduplicates_source_rows() -> None:
    importer, _ = _importer()
    base = _snapshot(
        _decision("term-solitude", "Solitude", "独孤城"),
        _decision("term-whiterun", "Whiterun", "雪漫城"),
        _decision("term-review", "Review", "待审", status=DecisionStatus.REVIEW_REQUIRED),
    )

    preview = importer.preview(
        "project-1",
        "variant-1",
        base,
        _source((" Whiterun ", "白漫城"), ("WHITERUN", "白漫城"), ("Riverwood", "河木镇")),
    )
    mappings = {item.original: item for item in preview.content.mappings}

    assert tuple(mappings) == ("Solitude", "Whiterun")
    assert mappings["Whiterun"].translation == "白漫城"
    assert mappings["Whiterun"].base_translation == "雪漫城"
    assert mappings["Solitude"].translation == mappings["Solitude"].base_translation == "独孤城"
    assert preview.source_entry_count == 3
    assert preview.source_term_count == 2
    assert preview.duplicate_entry_count == 1
    assert preview.matched_term_count == 1
    assert preview.changed_mapping_count == 1
    assert preview.source_only_term_count == 1
    assert preview.conflicts == ()


def test_preview_keeps_base_for_source_translation_conflict_and_ambiguous_base_scopes() -> None:
    importer, _ = _importer()
    base = _snapshot(
        _decision("term-whiterun-project", "Whiterun", "雪漫城"),
        _decision("term-riften-project", "Riften", "裂谷城"),
        _decision("term-riften-plugin", "Riften", "裂谷", TermScope.plugin("A.esp")),
    )

    preview = importer.preview(
        "project-1",
        "variant-1",
        base,
        _source(("Whiterun", "白漫"), ("Whiterun", "白漫城"), ("Riften", "里夫顿")),
    )
    mappings = {item.term_key: item for item in preview.content.mappings}

    assert all(item.translation == item.base_translation for item in mappings.values())
    assert preview.changed_mapping_count == 0
    assert preview.conflict_count == 2
    assert {item.kind for item in preview.conflicts} == {
        TerminologyProfileImportConflictKind.SOURCE_TRANSLATIONS,
        TerminologyProfileImportConflictKind.BASE_SCOPES,
    }


def test_preview_rejects_non_ready_base_and_empty_source() -> None:
    importer, _ = _importer()
    unavailable = EffectiveTerminologySnapshot(
        "project-1",
        "variant-1",
        EffectiveSnapshotStatus.UNAVAILABLE,
        diagnostics=("missing",),
    )

    with pytest.raises(TerminologyProfileImportError, match="must be ready"):
        importer.preview("project-1", "variant-1", unavailable, _source(("Whiterun", "白漫城")))
    with pytest.raises(TerminologyProfileImportError, match="empty"):
        importer.preview("project-1", "variant-1", _snapshot(_decision("w", "Whiterun", "雪漫")), _source())


def test_create_and_publish_uses_initial_content_and_selects_only_when_requested() -> None:
    importer, profiles = _importer()
    preview = importer.preview(
        "project-1",
        "variant-1",
        _snapshot(_decision("w", "Whiterun", "雪漫城")),
        _source(("Whiterun", "白漫城")),
    )

    unselected = importer.create_and_publish("project-1", "variant-1", "白漫方案", preview)
    assert unselected.profile.draft_revision == 0
    assert unselected.profile.draft == preview.content
    assert unselected.published.revision == 1
    assert unselected.selection is None
    assert profiles.selected_revision("project-1", "variant-1") is None

    selected = importer.create_and_publish("project-1", "variant-1", "当前方案", preview, select=True)
    assert selected.selection is not None
    assert profiles.selected_revision("project-1", "variant-1") == selected.published
