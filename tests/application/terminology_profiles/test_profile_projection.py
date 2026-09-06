from __future__ import annotations

from transbridge.application.terminology_profiles import (
    ProfileEntryOverride,
    ProfileOccurrenceBinding,
    ProfileTermMapping,
    TerminologyProfileContent,
    TerminologyProfileProjector,
    logical_term_key,
)


def test_switching_a_to_b_to_a_is_deterministic_and_does_not_change_common_text() -> None:
    projector = TerminologyProfileProjector()
    common = "前往雪漫城拜访领主。"
    profile_a = TerminologyProfileContent(
        mappings=(ProfileTermMapping("Whiterun", "雪漫城", "雪漫城"),),
    )
    profile_b = TerminologyProfileContent(
        mappings=(ProfileTermMapping("Whiterun", "白漫城", "雪漫城"),),
    )

    first_a = projector.project(
        entry_key="e1", original="Visit Whiterun.", common_translation=common, content=profile_a
    )
    result_b = projector.project(
        entry_key="e1", original="Visit Whiterun.", common_translation=common, content=profile_b
    )
    second_a = projector.project(
        entry_key="e1", original="Visit Whiterun.", common_translation=common, content=profile_a
    )

    assert first_a.translation == common
    assert result_b.translation == "前往白漫城拜访领主。"
    assert second_a == first_a
    assert common == "前往雪漫城拜访领主。"


def test_historical_recognition_requires_source_evidence_and_unique_target_occurrence() -> None:
    projector = TerminologyProfileProjector()
    content = TerminologyProfileContent(
        mappings=(ProfileTermMapping("Whiterun", "白漫", "雪漫"),),
    )

    no_source = projector.project(
        entry_key="e1",
        original="Visit Solitude",
        common_translation="去雪漫",
        content=content,
    )
    repeated = projector.project(
        entry_key="e2",
        original="Whiterun and Whiterun",
        common_translation="雪漫通往雪漫",
        content=content,
    )

    assert no_source.translation == "去雪漫"
    assert repeated.translation == "雪漫通往雪漫"
    assert {item.code for item in repeated.diagnostics} == {"historical_translation_ambiguous"}


def test_explicit_binding_survives_other_same_text_but_detects_stale_common_translation() -> None:
    projector = TerminologyProfileProjector()
    key = logical_term_key("Whiterun")
    content = TerminologyProfileContent(
        mappings=(ProfileTermMapping("Whiterun", "白漫", "雪漫"),),
        bindings=(ProfileOccurrenceBinding("e1", key, 0, 2, "雪漫"),),
    )

    result = projector.project(
        entry_key="e1",
        original="Whiterun and Whiterun",
        common_translation="雪漫通往雪漫",
        content=content,
    )
    stale = projector.project(entry_key="e1", original="Whiterun", common_translation="前往雪漫", content=content)

    assert result.translation == "白漫通往雪漫"
    assert stale.translation == "前往雪漫"
    assert {item.code for item in stale.diagnostics} == {"binding_text_changed"}


def test_entry_override_has_precedence() -> None:
    projector = TerminologyProfileProjector()
    content = TerminologyProfileContent(
        mappings=(ProfileTermMapping("Whiterun", "白漫", "雪漫"),),
        overrides=(ProfileEntryOverride("e1", "专用译文"),),
    )

    result = projector.project(entry_key="e1", original="Whiterun", common_translation="雪漫", content=content)

    assert result.translation == "专用译文"
    assert result.used_override is True


def test_matching_plugin_scope_shadows_project_mapping_for_the_same_term() -> None:
    projector = TerminologyProfileProjector()
    content = TerminologyProfileContent(
        mappings=(
            ProfileTermMapping("Dragon", "巨龙", "龙"),
            ProfileTermMapping("Dragon", "天际龙", "龙", scope_kind="plugin", plugin_id="Skyrim.esm"),
        ),
    )

    plugin = projector.project(
        entry_key="e1",
        original="Dragon",
        common_translation="龙",
        content=content,
        plugin_id="Skyrim.esm",
    )
    other = projector.project(
        entry_key="e2",
        original="Dragon",
        common_translation="龙",
        content=content,
        plugin_id="Update.esm",
    )

    assert plugin.translation == "天际龙"
    assert other.translation == "巨龙"


def test_overlapping_historical_candidates_are_not_guessed() -> None:
    projector = TerminologyProfileProjector()
    content = TerminologyProfileContent(
        mappings=(
            ProfileTermMapping("Dark Brotherhood", "暗黑兄弟会", "黑暗兄弟会"),
            ProfileTermMapping("Brotherhood", "兄弟会", "兄弟会"),
        ),
    )

    result = projector.project(
        entry_key="e1",
        original="Join the Dark Brotherhood",
        common_translation="加入黑暗兄弟会",
        content=content,
    )

    assert result.translation == "加入黑暗兄弟会"
    assert {item.code for item in result.diagnostics} == {"replacement_overlap"}


def test_one_ambiguous_term_fails_closed_for_the_entire_entry() -> None:
    projector = TerminologyProfileProjector()
    content = TerminologyProfileContent(
        mappings=(
            ProfileTermMapping("Whiterun", "白漫", "雪漫"),
            ProfileTermMapping("Jarl", "城主", "领主"),
        ),
    )

    result = projector.project(
        entry_key="e1",
        original="The Jarl of Whiterun met another Jarl",
        common_translation="雪漫领主会见了另一位领主",
        content=content,
    )

    assert result.translation == "雪漫领主会见了另一位领主"
    assert result.changed_term_keys == ()
    assert {item.code for item in result.diagnostics} == {"historical_translation_ambiguous"}


def test_source_match_without_registered_base_translation_is_visible_and_fails_closed() -> None:
    result = TerminologyProfileProjector().project(
        entry_key="e1",
        original="Travel to Whiterun",
        common_translation="前往天际的城市",
        content=TerminologyProfileContent(mappings=(ProfileTermMapping("Whiterun", "白漫", "雪漫"),)),
    )

    assert result.translation == "前往天际的城市"
    assert {item.code for item in result.diagnostics} == {"historical_translation_not_found"}
