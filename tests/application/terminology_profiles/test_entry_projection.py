from __future__ import annotations

import pytest

from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.application.terminology_profiles import (
    FrozenProfileWriteProjection,
    ProfileTermMapping,
    PublishedTerminologyProfile,
    TerminologyProfileContent,
    TerminologyProfileProjector,
)
from transbridge.application.terminology_profiles.entry_projection import ProfileEntryProjector
from transbridge.application.terminology_profiles.projection import SourceTermIndex, source_contains
from transbridge.converter.translation_entry import TranslationEntry


@pytest.mark.parametrize(
    "source",
    [
        "Dragonborn and DRAGON",
        "Visit Whiterun now",
        "foo-bar",
        "XC++",
        "xElf族y",
        "dragon and 龙裔",
        "Sovngarde's gate",
        "(A+B) and New    York",
        "nothing",
        "",
    ],
)
def test_source_candidate_index_matches_the_full_recognition_scan(source) -> None:
    terms = ("Dragon", "Dragonborn", "Whiterun", "foo-bar", "C++", "Elf族", "龙裔", "Sovngarde", "A+B", "New York")
    expected = tuple(term for term in terms if source_contains(source, term))

    assert SourceTermIndex(terms).matching_originals(source) == expected


def test_preview_and_write_honor_the_same_explicit_plugin_scope() -> None:
    entry = TranslationEntry(
        "e",
        "e",
        "Dragon",
        "OldDragon",
        1,
        None,
        form_id_with_plugin="00000001|inferred.esm",
        metadata=(("terminology_plugin_id", " explicit.esm "),),
    )
    mappings = tuple(
        ProfileTermMapping("Dragon", translation, "OldDragon", scope_kind="plugin", plugin_id=plugin)
        for plugin, translation in (("inferred.esm", "InferredDragon"), ("explicit.esm", "ExplicitDragon"))
    )
    content = TerminologyProfileContent(mappings)
    profile = PublishedTerminologyProfile("p", "project", 1, "Names", content.content_digest, content, "2026-10-14")
    base = EffectiveTerminologySnapshot(
        "project",
        "variant",
        EffectiveSnapshotStatus.READY,
        "base",
        "b" * 64,
        tuple(
            TermDecision(
                str(index),
                "project",
                "variant",
                "Dragon",
                "dragon",
                "OldDragon",
                TermScope.plugin(mapping.plugin_id),
                DecisionStatus.ADOPTED,
            )
            for index, mapping in enumerate(mappings)
        ),
    )

    preview = ProfileEntryProjector(profile, base).project(entry)
    written, diagnostics = FrozenProfileWriteProjection(
        "project", "variant", profile, TerminologyProfileProjector(), base
    ).project_entries((entry,))

    assert preview.translation == written[0].translation == "ExplicitDragon"
    assert preview.diagnostics == diagnostics == ()
    assert entry.translation == "OldDragon"


def _scheme(identifier, mappings, revision=1):
    content = TerminologyProfileContent(tuple(mappings))
    return PublishedTerminologyProfile(
        identifier, "project", revision, identifier, content.content_digest, content, "now"
    )


def test_actual_scheme_is_the_source_not_target_historical_base_and_repeats_all_change():
    source = _scheme("A", [ProfileTermMapping("Whiterun", "雪漫")])
    target = _scheme("B", [ProfileTermMapping("Whiterun", "白漫", "不相关的历史译名")])
    matched = TranslationEntry("1", "1", "Visit Whiterun", "雪漫通往雪漫", 1, None)
    unrelated = TranslationEntry("2", "2", "Visit another city", "雪漫通往雪漫", 1, None)
    projector = ProfileEntryProjector(target, None, source_profile=source)
    assert projector.project(matched).translation == "白漫通往白漫"
    assert projector.project(unrelated).translation == unrelated.translation
    assert matched.original == "Visit Whiterun"
    assert matched.translation == "雪漫通往雪漫"
    written, diagnostics = FrozenProfileWriteProjection(
        "project", "variant", target, TerminologyProfileProjector(), source_profile=source
    ).project_entries((matched, unrelated))
    assert [entry.translation for entry in written] == ["白漫通往白漫", "雪漫通往雪漫"]
    assert [entry.original for entry in written] == [matched.original, unrelated.original]
    assert diagnostics == ()


@pytest.mark.parametrize("text", ["", "未采用方案旧译名", "Whiterun"])
def test_actual_scheme_missing_spelling_or_empty_translation_is_a_quiet_noop(text):
    source = _scheme("A", [ProfileTermMapping("Whiterun", "雪漫")])
    target = _scheme("B", [ProfileTermMapping("Whiterun", "白漫")])
    entry = TranslationEntry("1", "1", "Whiterun", text, 0, None)
    result = ProfileEntryProjector(target, None, source_profile=source).project(entry)
    assert result.translation == text
    assert result.diagnostics == ()
    assert result.changed_term_keys == ()


def test_transition_is_simultaneous_and_same_profile_revision_roundtrip_returns_saved_body():
    source = _scheme("A", [ProfileTermMapping("One", "甲"), ProfileTermMapping("Two", "乙")])
    target = _scheme("B", [ProfileTermMapping("One", "乙"), ProfileTermMapping("Two", "丙")])
    entry = TranslationEntry("1", "1", "One and Two", "甲和乙", 1, None)
    assert ProfileEntryProjector(target, None, source_profile=source).project(entry).translation == "乙和丙"
    assert ProfileEntryProjector(source, None, source_profile=source).project(entry).translation == "甲和乙"
    revised = _scheme("A", [ProfileTermMapping("One", "新甲"), ProfileTermMapping("Two", "乙")], revision=2)
    assert ProfileEntryProjector(revised, None, source_profile=source).project(entry).translation == "新甲和乙"


def test_transition_common_keys_preserve_plugin_scope_and_unmapped_terms():
    source = _scheme(
        "A",
        [
            ProfileTermMapping("Dragon", "龙"),
            ProfileTermMapping("Dragon", "插件龙", scope_kind="plugin", plugin_id="A.esm"),
            ProfileTermMapping("Missing", "缺词"),
        ],
    )
    target = _scheme(
        "B",
        [
            ProfileTermMapping("Dragon", "巨龙"),
            ProfileTermMapping("Dragon", "插件巨龙", scope_kind="plugin", plugin_id="A.esm"),
        ],
    )
    entry = TranslationEntry("1", "1", "Dragon Missing", "插件龙与龙和缺词", 1, None, form_id_with_plugin="1|A.esm")
    result = ProfileEntryProjector(target, None, source_profile=source).project(entry)
    assert result.translation == "插件巨龙与龙和缺词"
    assert result.diagnostics == ()


def test_unassociated_body_uses_current_base_spelling_not_target_creation_history():
    target = _scheme("B", [ProfileTermMapping("Dragon", "巨龙", "过期创建时译名")])
    base = EffectiveTerminologySnapshot(
        "project",
        "variant",
        EffectiveSnapshotStatus.READY,
        "base-current",
        "b" * 64,
        (
            TermDecision(
                "d", "project", "variant", "Dragon", "dragon", "龙", TermScope.project(), DecisionStatus.ADOPTED
            ),
        ),
    )
    entry = TranslationEntry("1", "1", "Dragon", "龙与龙", 1, None)
    result = ProfileEntryProjector(target, base).project(entry)
    assert result.translation == "巨龙与巨龙"
    assert result.diagnostics == ()
    assert entry.translation == "龙与龙"


def test_base_transition_converts_only_common_keys_and_preserves_missing_and_target_only_terms():
    target = _scheme(
        "B", [ProfileTermMapping("Dragon", "巨龙", "过期龙"), ProfileTermMapping("TargetOnly", "目标新词", "目标旧词")]
    )
    base = EffectiveTerminologySnapshot(
        "project",
        "variant",
        EffectiveSnapshotStatus.READY,
        "base-current",
        "b" * 64,
        tuple(
            TermDecision(
                name,
                "project",
                "variant",
                name,
                name.casefold(),
                translation,
                TermScope.project(),
                DecisionStatus.ADOPTED,
            )
            for name, translation in (("Dragon", "龙"), ("Missing", "缺词"))
        ),
    )
    entry = TranslationEntry("1", "1", "Dragon Missing TargetOnly", "龙与缺词和目标旧词", 1, None)
    result = ProfileEntryProjector(target, base).project(entry)
    assert result.translation == "巨龙与缺词和目标旧词"
    assert result.diagnostics == ()


def test_base_plugin_scope_without_target_pair_shadows_project_replacement():
    target = _scheme("B", [ProfileTermMapping("Dragon", "巨龙")])
    base = EffectiveTerminologySnapshot(
        "project",
        "variant",
        EffectiveSnapshotStatus.READY,
        "base-current",
        "b" * 64,
        (
            TermDecision(
                "global", "project", "variant", "Dragon", "dragon", "龙", TermScope.project(), DecisionStatus.ADOPTED
            ),
            TermDecision(
                "plugin",
                "project",
                "variant",
                "Dragon",
                "dragon",
                "插件龙",
                TermScope.plugin("A.esm"),
                DecisionStatus.ADOPTED,
            ),
        ),
    )
    entry = TranslationEntry("1", "1", "Dragon", "插件龙", 1, None, form_id_with_plugin="1|A.esm")
    result = ProfileEntryProjector(target, base).project(entry)
    assert result.translation == "插件龙"
    assert result.diagnostics == ()
