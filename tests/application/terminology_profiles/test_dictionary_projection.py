# See LICENSE and NOTICE for TransBridge licensing and attribution terms.

from dataclasses import replace
import json

import pytest

from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.identity import normalize_original
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.application.terminology_profiles.dictionary_projection import DictionaryTextProjection
from transbridge.application.terminology_profiles.models import (
    ProfileEntryOverride,
    ProfileOccurrenceBinding,
    ProfileTermMapping,
    PublishedTerminologyProfile,
    TerminologyProfileContent,
)
from transbridge.converter.translation_entry import TranslationEntry


def _entry(original, translation="", **kwargs):
    return TranslationEntry("entry", "entry", original, translation, 1, None, **kwargs)


def _profile(*mappings, overrides=(), bindings=()):
    content = TerminologyProfileContent(mappings, overrides, bindings)
    return PublishedTerminologyProfile("B", "project", 1, "方案 B", content.content_digest, content, "now")


def _snapshot(*terms):
    return EffectiveTerminologySnapshot(
        "project",
        "variant",
        EffectiveSnapshotStatus.READY,
        "version-1",
        "default-digest",
        tuple(
            TermDecision(
                str(index),
                "project",
                "variant",
                original,
                normalize_original(original),
                translation,
                scope[0] if scope else TermScope.project(),
                DecisionStatus.ADOPTED,
            )
            for index, (original, translation, *scope) in enumerate(terms)
        ),
    )


def test_default_scheme_passes_verbatim_without_terminology():
    projection = DictionaryTextProjection(None, None, to_default=True)
    assert projection.translate(_entry("Whiterun"), "  白漫  ") == "  白漫  "
    assert projection.checkpoint_identity == ("default",)


def test_dictionary_roundtrip_uses_current_default_and_actual_scheme_and_preserves_input():
    entry = _entry("Travel from Whiterun to Whiterun", "从白漫城前往白漫城")
    base = _snapshot(("Whiterun", "雪漫城"))
    profile = _profile(ProfileTermMapping("Whiterun", "白漫城", "过期译名"))

    imported = DictionaryTextProjection(profile, base, to_default=True).translate(entry, entry.translation)
    applied = DictionaryTextProjection(profile, base, to_default=False).translate(entry, imported)

    assert imported == "从雪漫城前往雪漫城"
    assert applied == entry.translation == "从白漫城前往白漫城"
    assert entry.original == "Travel from Whiterun to Whiterun"


def test_unrelated_original_passes_even_if_translation_contains_a_paired_spelling(caplog):
    profile = _profile(ProfileTermMapping("Whiterun", "白漫"))
    base = _snapshot(("Whiterun", "雪漫"))
    result = DictionaryTextProjection(profile, base, to_default=True).translate(_entry("Whiterunner"), "白漫")

    assert result == "白漫"
    assert not caplog.records


def test_explicit_plugin_scope_takes_priority_and_inactive_shadow_blocks_project_fallback(caplog):
    entry = _entry(
        "Dragon",
        form_id_with_plugin="00000001|other.esm",
        metadata=(("terminology_plugin_id", " explicit.esm "),),
    )
    base = _snapshot(("Dragon", "龙"), ("Dragon", "插件龙", TermScope.plugin("explicit.esm")))
    profile = _profile(
        ProfileTermMapping("Dragon", "巨龙"),
        ProfileTermMapping("Dragon", "插件巨龙", scope_kind="plugin", plugin_id="explicit.esm"),
    )
    projection = DictionaryTextProjection(profile, base, to_default=True)
    assert projection.translate(entry, "插件巨龙") == "插件龙"
    assert projection.translate(replace(entry, metadata=()), "巨龙") == "龙"

    suppressed = replace(base.decisions[1], suppressed=True)
    shadowed_base = replace(base, decisions=(base.decisions[0], suppressed))
    shadowed = DictionaryTextProjection(profile, shadowed_base, to_default=True)
    assert shadowed.translate(entry, "插件巨龙") is None
    assert "term_mapping_unavailable" in caplog.text


@pytest.mark.parametrize("to_default", [True, False])
@pytest.mark.parametrize("missing_in", ["default", "scheme"])
def test_known_one_sided_term_skips_whole_sentence(to_default, missing_in, caplog):
    default_terms = [("Whiterun", "雪漫")]
    profile_terms = [ProfileTermMapping("Whiterun", "白漫")]
    if missing_in == "scheme":
        default_terms.append(("Jarl", "领主"))
    else:
        profile_terms.append(ProfileTermMapping("Jarl", "城主", "领主"))
    base = _snapshot(*default_terms)
    profile = _profile(*profile_terms)
    projection = DictionaryTextProjection(profile, base, to_default=to_default)
    text = "白漫的城主" if to_default else "雪漫的领主"

    assert projection.translate(_entry("The Jarl of Whiterun"), text) is None
    assert "term_mapping_unavailable" in caplog.text


@pytest.mark.parametrize("to_default,before,after", [(True, "白漫", "雪漫"), (False, "雪漫", "白漫")])
def test_historical_default_only_works_when_project_has_no_published_version(to_default, before, after, caplog):
    base = EffectiveTerminologySnapshot("project", "variant", EffectiveSnapshotStatus.NO_PROJECT_VERSION)
    profile = _profile(ProfileTermMapping("Whiterun", "白漫", "雪漫"))
    entry = _entry("Whiterun")

    assert DictionaryTextProjection(profile, base, to_default=to_default).translate(entry, before) == after

    corrupt = replace(base, status=EffectiveSnapshotStatus.CORRUPT, diagnostics=("unreadable asset",))
    assert DictionaryTextProjection(profile, corrupt, to_default=to_default).translate(entry, before) is None
    assert "base_terminology_snapshot_unavailable" in caplog.text


@pytest.mark.parametrize("text", ["白漫和城主和城主", "白漫和大人", "白漫和领主和领主"])
def test_mismatch_or_missing_location_skips_even_when_another_term_can_be_converted(text, caplog):
    profile = _profile(ProfileTermMapping("Whiterun", "白漫"), ProfileTermMapping("Jarl", "城主"))
    base = _snapshot(("Whiterun", "雪漫"), ("Jarl", "领主"))

    projected = DictionaryTextProjection(profile, base, to_default=True).translate(_entry("The Jarl of Whiterun"), text)

    assert projected is None
    assert "occurrence_alignment_required" in caplog.text


@pytest.mark.parametrize("text", ["一同见到国王", "一同见到君主"])
def test_two_terms_cannot_both_own_the_same_translated_span(text):
    profile = _profile(ProfileTermMapping("Emperor", "君主"), ProfileTermMapping("King", "君主"))
    base = _snapshot(("Emperor", "国王"), ("King", "国王"))

    assert DictionaryTextProjection(profile, base, to_default=True).translate(_entry("Emperor and King"), text) is None


def test_target_spelling_shared_with_an_unchanged_term_is_ambiguous():
    profile = _profile(ProfileTermMapping("Dragon", "巨龙"), ProfileTermMapping("Serpent", "龙"))
    base = _snapshot(("Dragon", "龙"), ("Serpent", "龙"))
    projection = DictionaryTextProjection(profile, base, to_default=True)

    assert projection.translate(_entry("Dragon and Serpent"), "龙") is None


def test_overlapping_literal_occurrences_are_ambiguous_even_for_the_same_word():
    profile = _profile(ProfileTermMapping("Laugh", "哈哈"))
    base = _snapshot(("Laugh", "笑"))

    assert DictionaryTextProjection(profile, base, to_default=True).translate(_entry("Laugh!"), "哈哈哈！") is None


def test_dictionary_can_use_a_current_word_binding_without_using_full_entry_override():
    entry = _entry("Whiterun meets Renwhiterun")
    mapping = ProfileTermMapping("Whiterun", "白漫")
    profile = _profile(
        mapping,
        overrides=(ProfileEntryOverride(entry.identity.serialize(), "方案整句特例"),),
        bindings=(ProfileOccurrenceBinding(entry.identity.serialize(), mapping.term_key, 0, 2, "雪漫"),),
    )
    base = _snapshot(("Whiterun", "雪漫"))
    projection = DictionaryTextProjection(profile, base, to_default=False)

    assert projection.translate(entry, "雪漫遇见雷雪漫") == "白漫遇见雷雪漫"
    assert projection.translate(entry, "新雪漫遇见雷雪漫") is None


def test_binding_does_not_modify_a_term_shared_by_both_schemes():
    entry = _entry("Whiterun")
    mapping = ProfileTermMapping("Whiterun", "雪漫")
    profile = _profile(
        mapping,
        bindings=(ProfileOccurrenceBinding(entry.identity.serialize(), mapping.term_key, 0, 2, "白漫"),),
    )
    base = _snapshot(("Whiterun", "雪漫"))

    assert DictionaryTextProjection(profile, base, to_default=False).translate(entry, "白漫") == "白漫"


def test_checkpoint_identity_depends_on_profile_revision_and_actual_default_snapshot():
    profile = _profile(ProfileTermMapping("Whiterun", "白漫"))
    base = _snapshot(("Whiterun", "雪漫"))
    first = DictionaryTextProjection(profile, base, to_default=True).checkpoint_identity

    assert json.loads(json.dumps(first)) == list(first)
    assert DictionaryTextProjection(profile, base, to_default=False).checkpoint_identity == first
    assert DictionaryTextProjection(replace(profile, revision=2), base, to_default=True).checkpoint_identity != first
    changed_base = replace(base, version_id="version-2")
    assert DictionaryTextProjection(profile, changed_base, to_default=True).checkpoint_identity != first
