from dataclasses import replace

import pytest

from transbridge.application.dialogue.index import build_dialogue_index
from transbridge.application.dialogue.loading import DialogueIndexLoader
from transbridge.application.io.contracts import FormatId, SourceDescriptor, SourceSnapshot
from transbridge.application.io.dialogue_question_order import order_topic_questions
from transbridge.application.io.dialogue_reading_view import ReadingEntry, build_dialogue_reading_view
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.parser.plugin.dialogue_catalog import DialogueCatalog, DialogueRecord
from transbridge.parser.plugin.dialogue_order_evidence import DialogueOrderEvidence, DialogueOrderRecord


def _entry(field="NAM1", *, kind="INFO", form="00000020", index=1, original="text", namespace="fixture"):
    key = f"Topic:{form}|{index}~{kind}:{field}"
    return TranslationEntry(
        id=key,
        key=key,
        original=original,
        translation="",
        stage=0,
        context=f"{kind}:{field}|00000001",
        entry_key=EntryKey(SourceNamespace(namespace), key),
        metadata=(("plugin.parent_dial_formid", "00000010"),),
    )


def _six_rows():
    full = _entry("FULL", kind="DIAL", form="00000010", original="Fire and Blood")
    override_reply = _entry(form="00000020")
    override_prompt = _entry("RNAM", form="00000020", original="Another question")
    first_reply = _entry(form="00000030", index=2, original="First physical response")
    second_reply = _entry(form="00000030", index=1, original="Second physical response")
    third_reply = _entry(form="00000040", original="Third default response")
    return full, override_reply, override_prompt, first_reply, second_reply, third_reply


def _evidence():
    return DialogueOrderEvidence(
        records=(
            DialogueOrderRecord("DIAL", "00000010", 0, quest_id="00000001"),
            DialogueOrderRecord("INFO", "00000020", 1, "00000010", "00000001"),
            DialogueOrderRecord("INFO", "00000030", 2, "00000010", "00000001"),
            DialogueOrderRecord("INFO", "00000040", 3, "00000010", "00000001"),
        )
    )


def test_all_six_rows_keep_default_full_replies_before_explicit_override():
    full, reply, prompt, first, second, third = entries = _six_rows()
    before = tuple(entry.to_dict() for entry in entries)
    ordered = order_topic_questions(entries)
    assert ordered == (full, first, second, third, prompt, reply)
    assert all(actual is expected for actual, expected in zip(ordered, (full, first, second, third, prompt, reply)))
    assert tuple(entry.to_dict() for entry in entries) == before


@pytest.mark.parametrize("original", ["", " \r\n\t"])
def test_absent_visible_full_preserves_physical_info_group_order(original):
    full, reply, prompt, first, second, third = _six_rows()
    full = replace(full, original=original)
    assert order_topic_questions([reply, prompt, first, second, third, full]) == (
        full,
        prompt,
        reply,
        first,
        second,
        third,
    )
    assert order_topic_questions([reply, prompt, first, second, third]) == (prompt, reply, first, second, third)


@pytest.mark.parametrize("original", ["", " \r\n\t"])
def test_empty_override_remains_a_default_info_without_dropping_its_prompt(original):
    full, reply, prompt, first, second, third = _six_rows()
    prompt = replace(prompt, original=original)
    assert order_topic_questions([full, reply, prompt, first, second, third]) == (
        full,
        prompt,
        reply,
        first,
        second,
        third,
    )


def test_default_and_override_cohorts_each_keep_their_own_physical_info_order():
    full = _entry("FULL", kind="DIAL", form="00000010")
    override_b = _entry("RNAM", form="000000B0")
    default_b = _entry(form="000000D0")
    override_a = _entry("RNAM", form="000000A0")
    default_a = _entry(form="000000C0")
    assert order_topic_questions([override_b, default_b, full, override_a, default_a]) == (
        full,
        default_b,
        default_a,
        override_b,
        override_a,
    )


def test_unrelated_rows_and_other_info_fields_keep_their_slots():
    first = _entry(index=2)
    other_field = _entry("NAM2")
    unrelated = _entry("FULL", kind="BOOK", form="00000090")
    prompt = _entry("RNAM")
    second = _entry(index=1)
    assert order_topic_questions([first, other_field, unrelated, prompt, second]) == (
        prompt,
        other_field,
        unrelated,
        first,
        second,
    )


def test_namespace_and_quest_disambiguate_same_info_formid():
    reply = _entry()
    other_source_prompt = _entry("RNAM", namespace="another")
    other_quest_prompt = replace(_entry("RNAM"), context="INFO:RNAM|00000002")
    assert order_topic_questions([reply, other_source_prompt, other_quest_prompt]) == (
        reply,
        other_source_prompt,
        other_quest_prompt,
    )


def test_invalid_or_context_conflicting_keys_cannot_supply_info_membership():
    reply = _entry()
    invalid = _entry("RNAM", form="not-an-id")
    conflicting = replace(_entry("RNAM"), context="INFO:NAM1|00000001")
    form_conflict = replace(_entry("RNAM"), form_id_with_plugin="00000030|fixture.esp")
    assert order_topic_questions([reply, invalid, conflicting, form_conflict]) == (
        reply,
        invalid,
        conflicting,
        form_conflict,
    )


def test_valid_explicit_formid_can_identify_rows_with_opaque_keys():
    reply = replace(_entry(), key="reply", entry_key=EntryKey(SourceNamespace("fixture"), "reply"))
    prompt = replace(_entry("RNAM"), key="prompt", entry_key=EntryKey(SourceNamespace("fixture"), "prompt"))
    reply = replace(reply, form_id_with_plugin="00000020|fixture.esp")
    prompt = replace(prompt, form_id_with_plugin="00000020|fixture.esp")
    assert order_topic_questions([reply, prompt]) == (prompt, reply)


def test_frozen_reading_entries_and_ordered_contexts_produce_identical_keys():
    entries = tuple(replace(entry, context=f"{index:08d}|{entry.context}") for index, entry in enumerate(_six_rows()))
    captured = tuple(
        ReadingEntry(entry.identity, entry.context, entry.metadata, bool(entry.original.strip())) for entry in entries
    )
    assert tuple(entry.identity for entry in order_topic_questions(entries)) == tuple(
        entry.identity for entry in order_topic_questions(captured)
    )
    assert ReadingEntry(entries[0].identity, entries[0].context, ()).has_original is False


def test_reading_cache_cannot_reuse_ranks_after_source_question_visibility_changes():
    entries = _six_rows()
    evidence = _evidence()
    captured = tuple(
        ReadingEntry(entry.identity, entry.context, entry.metadata, bool(entry.original.strip())) for entry in entries
    )
    visible = build_dialogue_reading_view(captured, evidence)
    assert build_dialogue_reading_view(captured, evidence) is visible
    hidden = (replace(captured[0], has_original=False), *captured[1:])
    updated = build_dialogue_reading_view(hidden, evidence)
    assert visible.ordered_keys != updated.ordered_keys
    assert updated.ordered_keys == tuple(entry.identity for entry in order_topic_questions(hidden))


def test_dialogue_and_scene_navigation_use_the_same_six_row_order():
    entries = _six_rows()
    catalog = DialogueCatalog((
        DialogueRecord("DIAL", "00000010", "FireAndBlood", "00000001"),
        DialogueRecord("SCEN", "00000011", "Scene", "00000001", topic_ids=("00000010",)),
    ))
    index = build_dialogue_index(entries, catalog=catalog)
    dial, scene = index.quests[0].topics
    expected = tuple(entry.identity for entry in order_topic_questions(entries))
    assert dial.entries == scene.entries == expected
    assert index.locations[entries[2].identity] == (0, 0, 4)


@pytest.mark.parametrize("visible_indices", [(1, 3, 4, 5), (0, 1, 3, 4, 5), (1, 2, 3, 4, 5)])
def test_loader_projects_complete_source_row_ranks_when_full_or_override_is_hidden(monkeypatch, visible_indices):
    entries = _six_rows()
    catalog = DialogueCatalog((DialogueRecord("DIAL", "00000010", "FireAndBlood", "00000001"),))
    evidence = _evidence()
    monkeypatch.setattr(
        "transbridge.application.dialogue.loading._snapshot_directory", lambda *_args: (catalog, evidence)
    )
    source = SourceSnapshot.from_bytes(SourceDescriptor("absent.esp"), FormatId.PLUGIN_SSE, b"captured")
    complete = tuple(
        ReadingEntry(entry.identity, entry.context, entry.metadata, bool(entry.original.strip())) for entry in entries
    )
    selected = tuple(entries[index] for index in visible_indices)
    visible_keys = {entry.identity for entry in selected}
    expected = tuple(entry.identity for entry in order_topic_questions(entries) if entry.identity in visible_keys)
    result = DialogueIndexLoader().build(selected, snapshot=source, reading_entries=complete)
    assert result.quests[0].topics[0].entries == expected
