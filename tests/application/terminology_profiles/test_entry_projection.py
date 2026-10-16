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
