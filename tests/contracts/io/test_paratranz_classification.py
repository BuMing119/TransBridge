"""Shared source-scoped classification selects only exact QUST:FULL originals."""

from dataclasses import replace

import pytest

from transbridge.application.categorized_upload import UploadSource, categorize_sources
from transbridge.application.io.paratranz_classification import classify_entries
from transbridge.application.io.paratranz_context_order import parse_ordered_context
from transbridge.converter.translation_entry import TranslationEntry


def entry(key, original="O", context="NPC_:FULL", **kwargs):
    return TranslationEntry(key, key, original, "Translated title", 1, context, **kwargs)


def title(quest="00000010", name="The Original Quest", *, context="QUST:FULL"):
    return entry(f"Quest:{quest}|1~{context}", name, context)


def line(quest="00000010", key="line"):
    return entry(key, "A line", f"INFO:NAM1|{quest}")


def dialogue_paths(entries):
    return {
        group.category_id: group.path
        for group in classify_entries(tuple(entries))
        if group.category_id.startswith("dialogue:")
    }


def test_quest_name_uses_full_original_not_translation_or_first_formid_match():
    entries = (title(name="Wrong log", context="QUST:CNAM"), line(), title())
    before = tuple(item.to_dict() for item in entries)
    assert dialogue_paths(entries) == {"dialogue:00000010": "任务/对话_[The Original Quest].json"}
    assert tuple(item.to_dict() for item in entries) == before


@pytest.mark.parametrize("names", [(), ("",), ("   ",), ("One", "Two")])
def test_missing_empty_or_ambiguous_title_falls_back_to_quest_id(names):
    entries = (line(), *(title(name=name) for name in names))
    assert dialogue_paths(entries) == {"dialogue:00000010": "任务/对话_[00000010].json"}


def test_repeated_identical_full_original_is_not_ambiguous():
    assert dialogue_paths((title(), title(), line()))["dialogue:00000010"] == "任务/对话_[The Original Quest].json"


def test_context_display_prefix_and_formid_case_are_handled():
    entries = (title("00abcdef", context="00000009|QUST:FULL"), line("00ABCDEF"))
    assert dialogue_paths(entries) == {"dialogue:00ABCDEF": "任务/对话_[The Original Quest].json"}


def test_full_formid_metadata_can_identify_a_quest_without_parsing_arbitrary_key():
    quest = entry("opaque", "Metadata Quest", "QUST:FULL", form_id_with_plugin="00000010|A.esm")
    assert dialogue_paths((quest, line()))["dialogue:00000010"] == "任务/对话_[Metadata Quest].json"
    assert (
        dialogue_paths((replace(quest, form_id_with_plugin=None), line()))["dialogue:00000010"]
        == "任务/对话_[00000010].json"
    )


@pytest.mark.parametrize("names", [("Same", "Same"), ("Same", "same"), ("A/B", "A\\B"), ("A" * 120, "A" * 121)])
def test_colliding_names_remain_separate_and_receive_quest_id_suffixes(names):
    entries = (title(name=names[0]), title("00000020", names[1]), line(), line("00000020", "other"))
    paths = dialogue_paths(entries)
    assert len({path.casefold() for path in paths.values()}) == 2
    assert "00000010" in paths["dialogue:00000010"]
    assert "00000020" in paths["dialogue:00000020"]
    assert paths == dialogue_paths(tuple(reversed(entries)))


def test_empty_sanitized_name_does_not_become_an_empty_filename():
    assert dialogue_paths((title(name="..."), line()))["dialogue:00000010"] == "任务/对话_[00000010].json"


def test_real_titles_that_shadow_generated_names_are_resolved_until_unique():
    names = ("Same", "Same", "Same_00000010", "Same_00000010_00000010")
    entries = tuple(
        item
        for index, name in enumerate(names, 1)
        for item in (title(f"{index * 16:08X}", name), line(f"{index * 16:08X}", str(index)))
    )
    paths = dialogue_paths(entries)
    assert len(set(paths.values())) == 4
    assert paths == dialogue_paths(tuple(reversed(entries)))
    assert paths["dialogue:00000010"] == "任务/对话_[Same_00000010_00000010_00000010].json"


def test_task_name_lookup_is_isolated_per_source_and_category_identity_remains_id():
    sources = (UploadSource("a", "A.esp", (title(), line())), UploadSource("b", "B.esp", (line(),)))
    files = categorize_sources(sources)
    assert {
        (file.source_id, file.category_id, file.path) for file in files if file.category_id.startswith("dialogue:")
    } == {
        ("a", "dialogue:00000010", "A.esp/任务/对话_[The Original Quest].json"),
        ("b", "dialogue:00000010", "B.esp/任务/对话_[00000010].json"),
    }


def test_unknown_contexts_and_unassociated_dialogue_are_not_dropped():
    entries = (entry("none", context=None), entry("unknown", context="CUSTOM"), line(""))
    groups = classify_entries(entries)
    assert {group.path for group in groups} == {"其他.json", "任务/对话_[未关联任务].json"}
    assert sum(len(group.entries) for group in groups) == len(entries)


def test_name_and_collision_plan_is_built_before_file_selection():
    entries = (title(name="Same"), title("00000020", "Same"), line(), line("00000020", "other"))
    files = categorize_sources((UploadSource("a", "A.esp", entries),))
    chosen = [file for file in files if file.category_id == "dialogue:00000010"]
    assert chosen[0].path == "A.esp/任务/对话_[Same_00000010].json"
    assert chosen[0].entries[0].identity == entries[2].identity


def test_dialogue_sorting_preserves_global_fallback_slots_and_local_entries():
    entries = (
        entry("later", context="INFO:NAM1|00000010", metadata=(("plugin.source_order", 8),)),
        entry("book", context="BOOK:FULL"),
        entry("first", context="INFO:NAM1|00000010", metadata=(("plugin.source_order", 2),)),
    )
    groups = classify_entries(entries)
    by_key = {item.key: item for group in groups for item in group.entries}
    assert {key: parse_ordered_context(item.context)[1] for key, item in by_key.items()} == {
        "first": 0,
        "book": 1,
        "later": 2,
    }
    assert [item.key for item in entries] == ["later", "book", "first"]
