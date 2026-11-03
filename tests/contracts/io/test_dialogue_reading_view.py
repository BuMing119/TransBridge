from dataclasses import replace

import pytest

from tests.dialogue_order_support import dialogue_bytes
from transbridge.application.io.dialogue_reading_view import ReadingEntry, build_dialogue_reading_view
from transbridge.application.io.identity import SourceNamespace
from transbridge.application.io.paratranz_classification import classify_entries
from transbridge.parser.plugin.dialogue_order_binary import DialogueOrderCancelled
from transbridge.parser.plugin.dialogue_order_evidence import (
    DialogueOrderRecord,
    read_dialogue_order_evidence,
)
from transbridge.parser.plugin_parser import PluginParser


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "fixture.esp"
    path.write_bytes(dialogue_bytes())
    parser = PluginParser()
    entries = tuple(parser.parse_plugin(path, skip_empty=False, raise_on_error=True))
    return entries, read_dialogue_order_evidence(parser.get_plugin())


def test_internal_reading_order_matches_classified_order_without_any_entry_changes(source):
    entries, evidence = source
    before = [entry.to_dict() for entry in entries]
    view = build_dialogue_reading_view(entries, evidence)
    by_key = {entry.identity: entry for entry in entries}
    visible = [by_key[key].original for key in view.ordered_keys if by_key[key].context.startswith(("DIAL:", "INFO:"))]
    classified = next(group for group in classify_entries(entries, dialogue_order=evidence) if "对话" in group.path)
    assert visible == [entry.original for entry in classified.entries]
    assert visible == ["First topic", "First reply", "Second reply", "Later topic", "Later reply"]
    assert view.topic_orders[entries[0].identity.namespace, "00000001"] == ("00000010", "00000020")
    assert [entry.to_dict() for entry in entries] == before
    assert set(view.ordered_keys) == set(by_key)
    with pytest.raises(TypeError):
        view.topic_orders[entries[0].identity.namespace, "00000001"] = ()


def test_worker_structure_is_sufficient_and_translation_edits_cannot_change_order(source):
    entries, evidence = source
    frozen = tuple(ReadingEntry(entry.identity, entry.context, entry.metadata) for entry in entries)
    expected = build_dialogue_reading_view(frozen, evidence)
    changed = [replace(entry, translation="New translation", stage=3) for entry in entries]
    assert build_dialogue_reading_view(changed, evidence) == expected


def test_full_source_rank_projects_to_subset_without_changing_info_response_order(source):
    entries, evidence = source
    view = build_dialogue_reading_view(entries, evidence)
    selected = {entry.identity for entry in entries if "reply" in entry.original}
    by_key = {entry.identity: entry for entry in entries}
    assert [by_key[key].original for key in view.ordered_keys if key in selected] == [
        "First reply",
        "Second reply",
        "Later reply",
    ]


def test_tree_includes_zero_output_topics_and_quests(source):
    entries, evidence = source
    evidence = replace(
        evidence,
        records=(
            *evidence.records,
            DialogueOrderRecord("DIAL", "00000030", 100, quest_id="00000001"),
            DialogueOrderRecord("DIAL", "00000040", 101, quest_id="00000002"),
        ),
    )
    view = build_dialogue_reading_view(entries, evidence)
    namespace = entries[0].identity.namespace
    assert set(view.topic_orders[namespace, "00000001"]) == {"00000010", "00000020", "00000030"}
    assert view.topic_orders[namespace, "00000002"] == ("00000040",)
    assert set(view.ordered_keys) == {entry.identity for entry in entries}


def test_bad_quest_keeps_source_baseline_and_does_not_drop_rows(source):
    entries, evidence = source
    evidence = replace(evidence, invalid_quests=("00000001",))
    view = build_dialogue_reading_view(entries, evidence)
    assert view.ordered_keys == tuple(entry.identity for entry in entries)
    assert view.topic_orders[entries[0].identity.namespace, "00000001"] == ("00000020", "00000010")


@pytest.mark.parametrize("mode", ["missing", "incomplete", "mixed", "duplicate"])
def test_unusable_source_does_not_introduce_a_rank(mode, source):
    entries, evidence = source
    if mode == "missing":
        evidence = None
    elif mode == "incomplete":
        evidence = replace(evidence, complete=False)
    elif mode == "mixed":
        extra = ReadingEntry(
            replace(entries[0].identity, namespace=SourceNamespace("other")), entries[0].context, entries[0].metadata
        )
        entries = (*entries, extra)
    else:
        entries = (*entries, entries[0])
    view = build_dialogue_reading_view(entries, evidence)
    assert view.ordered_keys == tuple(entry.identity for entry in entries)
    assert not view.topic_orders


def test_cancellation_never_publishes_partial_positions(source):
    entries, evidence = source
    with pytest.raises(DialogueOrderCancelled):
        build_dialogue_reading_view(entries, evidence, cancellation=lambda: True)


def test_shared_frozen_structure_reuses_positions_but_new_structure_rebuilds(source):
    entries, evidence = source
    frozen = tuple(ReadingEntry(entry.identity, entry.context, entry.metadata) for entry in entries)
    view = build_dialogue_reading_view(frozen, evidence)
    assert build_dialogue_reading_view(frozen, evidence) is view
    removed = frozen[1:]
    revised = build_dialogue_reading_view(removed, evidence)
    assert set(revised.ordered_keys) == {entry.identity for entry in removed}
    changed = replace(evidence, invalid_quests=("00000001",))
    fallback = build_dialogue_reading_view(frozen, changed)
    assert fallback.ordered_keys == tuple(entry.identity for entry in entries)
    with pytest.raises(DialogueOrderCancelled):
        build_dialogue_reading_view(frozen, evidence, cancellation=lambda: True)


def test_mutable_translation_entries_are_never_retained_as_reading_cache_inputs(source):
    entries, evidence = source
    first = build_dialogue_reading_view(entries, evidence)
    assert build_dialogue_reading_view(entries, evidence) is not first


def test_legacy_mutable_metadata_bypasses_reading_cache(source):
    entries, evidence = source
    frozen = tuple(ReadingEntry(entry.identity, entry.context, (("legacy", []),)) for entry in entries)
    first = build_dialogue_reading_view(frozen, evidence)
    assert build_dialogue_reading_view(frozen, evidence) is not first


def test_reading_cache_evicts_old_sources_and_bounds_retained_structures(source, monkeypatch):
    from transbridge.application.io import dialogue_reading_cache as cache

    monkeypatch.setattr(cache, "_LIMIT", 1)
    entries, evidence = source
    frozen = tuple(ReadingEntry(entry.identity, entry.context, entry.metadata) for entry in entries)
    view = build_dialogue_reading_view(frozen, evidence)
    build_dialogue_reading_view(frozen[1:], evidence)
    assert len(cache._cache) == 1
    assert build_dialogue_reading_view(frozen, evidence) is not view
    monkeypatch.setattr(cache, "_MAX_ITEMS", 0)
    other = frozen[:-1]
    first = build_dialogue_reading_view(other, evidence)
    assert build_dialogue_reading_view(other, evidence) is not first
