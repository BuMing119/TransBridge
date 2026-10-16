from dataclasses import FrozenInstanceError

import pytest

from transbridge.application.terminology_profiles import (
    ProfileOccurrenceBinding,
    ProfileTermMapping,
    TerminologyProfileContent,
    TerminologyProfileProjector,
    projection as projection_module,
)
from transbridge.application.terminology_profiles.entry_projection import ProfileEntryInput
from transbridge.application.terminology_profiles.projection import SourceTermIndex, source_contains
from transbridge.converter.translation_entry import TranslationEntry


def test_reused_projection_does_not_hash_or_rescan_immutable_content(monkeypatch):
    content = TerminologyProfileContent(
        tuple(ProfileTermMapping(f"Term{index}", f"New{index}", f"Old{index}") for index in range(1000))
    )
    projector = TerminologyProfileProjector()
    prepared = projector.prepare(content)

    def forbidden(*_args):
        raise AssertionError("prepared projection rescanned or hashed the complete content")

    monkeypatch.setattr(TerminologyProfileContent, "__hash__", forbidden)
    monkeypatch.setattr("transbridge.application.terminology_profiles.projection._profile_indexes", forbidden)
    assert projector.prepare(content) is prepared
    for index in (0, 50, 999):
        result = projector.project(
            content=content, entry_key=str(index), original=f"Term{index}", common_translation=f"Old{index}"
        )
        assert result.translation == f"New{index}"


@pytest.mark.parametrize("plugin", [None, "A.esm", "B.esm"])
def test_prepared_candidates_match_full_scan_with_scope_shadowing(plugin):
    mappings = tuple(
        ProfileTermMapping(original, f"New{index}", f"Old{index}", scope_kind=scope, plugin_id=scope_plugin)
        for index, (original, scope, scope_plugin) in enumerate((
            ("Dragon", "project", None),
            ("DRAGON", "plugin", "A.esm"),
            ("Dragonborn", "project", None),
            ("C++", "project", None),
            ("Elf族", "plugin", "B.esm"),
            ("New York", "project", None),
            ("Absent", "project", None),
        ))
    )
    content = TerminologyProfileContent(mappings)
    prepared = TerminologyProfileProjector().prepare(content)
    source = "DRAGON Dragonborn XC++ xElf族 New    York"
    plugin_names = {item.original.casefold() for item in mappings if item.plugin_id == plugin and plugin is not None}
    expected = tuple(
        sorted(
            (
                item
                for item in mappings
                if source_contains(source, item.original)
                and (
                    item.plugin_id == plugin
                    and item.scope_kind == "plugin"
                    or item.scope_kind == "project"
                    and item.original.casefold() not in plugin_names
                )
            ),
            key=lambda item: item.term_key,
        )
    )
    common = "/".join(item.base_translation for item in expected)
    result = prepared.project(entry_key="e", original=source, common_translation=common, plugin_id=plugin)
    assert result.translation == "/".join(item.translation for item in expected)
    assert result.changed_term_keys == tuple(sorted(item.term_key for item in expected))
    assert result.diagnostics == ()


def test_bound_project_mapping_cannot_bypass_plugin_shadowing():
    project = ProfileTermMapping("Dragon", "Project", "Old")
    plugin = ProfileTermMapping("Dragon", "Plugin", "Old", scope_kind="plugin", plugin_id="A.esm")
    content = TerminologyProfileContent(
        (project, plugin), bindings=(ProfileOccurrenceBinding("e", project.term_key, 0, 3, "Old"),)
    )
    result = (
        TerminologyProfileProjector()
        .prepare(content)
        .project(entry_key="e", original="Dragon", common_translation="Old", plugin_id="A.esm")
    )
    assert result.translation == "Old"
    assert [item.code for item in result.diagnostics] == ["binding_mapping_missing"]


def test_entry_capture_keeps_immutable_text_and_explicit_plugin_scope():
    entry = TranslationEntry(
        "e",
        "e",
        "Dragon",
        "Old",
        1,
        None,
        form_id_with_plugin="id|inferred.esm",
        metadata=(("terminology_plugin_id", "explicit.esm"),),
    )
    captured = ProfileEntryInput.capture(entry)
    with pytest.warns(DeprecationWarning):
        entry.translation = "Changed"
    assert captured.common_translation == "Old"
    assert captured.plugin_id == "explicit.esm"
    with pytest.raises(FrozenInstanceError):
        captured.common_translation = "Other"


def test_shared_ascii_prefix_uses_rarest_token_without_changing_matching(monkeypatch):
    terms = tuple(f"Term {index:06d}" for index in range(2000)) + ("Term", "C++", "Elf族")
    source = "Find Term 001999 and XC++ and xElf族"
    expected = tuple(term for term in terms if source_contains(source, term))
    index = SourceTermIndex(terms)
    checked = []
    original = projection_module._normalized_source_contains

    def contains(source, term):
        checked.append(term)
        return original(source, term)

    monkeypatch.setattr(projection_module, "_normalized_source_contains", contains)
    assert index.matching_originals(source) == expected
    assert len(checked) == 4
