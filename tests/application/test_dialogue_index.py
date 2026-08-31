from dataclasses import replace

import pytest

from tests.dialogue_catalog_support import dialogue_plugin
from tests.dialogue_support import dialogue_entries, dialogue_entry
from transbridge.application.dialogue.index import build_dialogue_index, source_unavailable_reason
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.parser.plugin.dialogue_catalog import DialogueCatalog, DialogueRecord, read_dialogue_catalog


def test_task_topics_and_multiple_responses_restore_source_order_without_mutation():
    original = dialogue_entries()
    shuffled = tuple(reversed(original))
    result = build_dialogue_index(shuffled)
    quest = result.quests[0]
    assert len(result.quests) == 1
    assert quest.label == "QUST {Quest} [00000001]"
    assert quest.topics[1].label == "DIAL {Topic} [00000010]"
    assert len(quest.topics) == 3
    assert quest.topics[1].entries == tuple(e.identity for e in original[1:4])
    assert result.locations[original[3].identity] == (0, 1, 2)
    assert shuffled == tuple(reversed(original))


def test_hydrated_entries_without_legacy_extra_fields_still_join_their_parent():
    entries = tuple(replace(e, form_id_with_plugin=None, editor_id="") for e in dialogue_entries())
    result = build_dialogue_index(entries)
    assert len(result.quests[0].topics) == 3
    assert result.quests[0].label == "QUST {Quest} [00000001]"
    assert result.quests[0].topics[1].label == "DIAL {Topic} [00000010]"
    assert result.quests[0].topics[1].entries == tuple(e.identity for e in entries[1:4])


def test_missing_topic_text_and_unknown_relationships_are_not_guessed():
    orphan = dialogue_entry(parent="00000099")
    unknown = replace(dialogue_entry(form="00000022", parent=None, quest=""), metadata=())
    result = build_dialogue_index([orphan, unknown])
    assert result.quests[0].topics[0].label == "DIAL [00000099]"
    assert result.quests[1].label == "QUST {未关联任务}"
    assert result.quests[1].topics[0].label == "DIAL {未关联话题}"
    assert "缺少父 DIAL" in result.quests[1].topics[0].tooltip


@pytest.mark.parametrize("metadata", [(), (("plugin.source_order", 1),)])
def test_incomplete_or_duplicate_order_preserves_input_order(metadata):
    first = dialogue_entry(form="00000020", order=1)
    second = replace(dialogue_entry(form="00000021", order=2), metadata=metadata)
    # No parent metadata on second: both are explicitly unknown, not joined by position.
    first = replace(first, metadata=(("plugin.source_order", 1),))
    result = build_dialogue_index([second, first])
    assert result.quests[0].topics[0].entries == (second.identity, first.identity)


def test_same_local_formids_in_two_source_namespaces_remain_separate():
    first = dialogue_entry()
    second = replace(first, entry_key=EntryKey(SourceNamespace("another-plugin"), first.key))
    result = build_dialogue_index([first, second])
    assert len(result.quests) == 2
    assert result.locations[first.identity] != result.locations[second.identity]


@pytest.mark.parametrize("format_id", ["xml.eet", "binary.eet", "xml.xt", "json.paratranz"])
def test_explicit_non_plugin_source_cannot_enable_context_by_a_stale_plugin_path(format_id):
    assert source_unavailable_reason(format_id=format_id, esp_path="stale.esp", eet_path="source.xml")


def test_plugin_with_eet_translation_overlay_is_available():
    assert source_unavailable_reason(format_id="plugin.sse", esp_path=None, eet_path="source.xml") is None
    assert source_unavailable_reason(format_id=None, esp_path="source.esp", eet_path="source.xml") is None
    assert "EET" in source_unavailable_reason(format_id=None, esp_path=None, eet_path="source.xml")
    assert build_dialogue_index([]).quests == ()


def test_catalog_labels_and_scene_contents_keep_canonical_entry_locations():
    entries = dialogue_entries()
    catalog = read_dialogue_catalog(dialogue_plugin())
    result = build_dialogue_index(reversed(entries), catalog=catalog)
    quest = result.quests[0]
    assert [topic.label for topic in quest.topics] == [
        "QUST {Quest} [00000001]",
        "DIAL {Topic10} [00000010]",
        "DIAL {Scene} [00000011]",
        "SCEN {TestScene} [00000012]",
        "SCEN {EmptyScene} [00000013]",
    ]
    assert quest.topics[3].entries == tuple(entry.identity for entry in entries[1:])
    assert quest.topics[4].entries == ()
    assert result.locations[entries[2].identity] == (0, 1, 1)
    assert result.locations[entries[5].identity] == (0, 2, 1)


def test_left_record_order_uses_formid_while_right_responses_keep_source_order():
    catalog = DialogueCatalog((
        DialogueRecord("SCEN", "0000000F", "SceneBeforeTopic", "00000001", topic_ids=("00000010",)),
        DialogueRecord("DIAL", "00000010", "InternalTopic", "00000001"),
    ))
    first = dialogue_entry(form="000000F0", order=1)
    second = dialogue_entry(form="00000020", order=2)
    quest = build_dialogue_index([second, first], catalog=catalog).quests[0]
    assert [topic.kind for topic in quest.topics] == ["SCEN", "DIAL"]
    assert quest.topics[1].entries == (first.identity, second.identity)


def test_scene_can_reference_a_topic_without_full_text_and_an_explicit_other_quest():
    entries = [
        dialogue_entry(quest="", parent="00000010"),
        dialogue_entry(form="00000021", quest="00000002", parent="00000011"),
    ]
    catalog = DialogueCatalog((
        DialogueRecord("SCEN", "00000012", "Scene", "00000001", topic_ids=("00000010", "00000011", "00000099")),
        DialogueRecord("DIAL", "00000010", quest_id="00000001", category="Scene"),
        DialogueRecord("DIAL", "00000011", quest_id="00000002"),
    ))
    result = build_dialogue_index(entries, catalog=catalog)
    scene = next(topic for quest in result.quests for topic in quest.topics if topic.kind == "SCEN")
    assert scene.entries == tuple(entry.identity for entry in entries)
    assert len(result.locations) == 2
    assert result.quests[result.locations[entries[1].identity][0]].identity[1] == "00000002"
    assert "00000099" in scene.tooltip


def test_mixed_source_namespaces_never_receive_ambiguous_raw_scene_records():
    first = dialogue_entry()
    other = replace(first, entry_key=EntryKey(SourceNamespace("another-plugin"), first.key))
    result = build_dialogue_index([first, other], catalog=read_dialogue_catalog(dialogue_plugin()))
    assert len(result.quests) == 2
    assert all(topic.kind == "DIAL" for quest in result.quests for topic in quest.topics)
    assert result.quests[0].topics[0].entries == (first.identity,)


def test_catalog_topic_without_quest_reuses_explicit_entry_quest_without_orphan_duplicate():
    catalog = DialogueCatalog((DialogueRecord("DIAL", "00000010", "Topic"),))
    result = build_dialogue_index([dialogue_entry()], catalog=catalog)
    assert len(result.quests) == 1
    assert len(result.quests[0].topics) == 1
