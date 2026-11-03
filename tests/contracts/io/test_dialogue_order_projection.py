from dataclasses import replace
import json

import pytest

from tests.dialogue_order_support import dialogue_bytes
from transbridge.application.categorized_upload import UploadSource, categorize_sources
from transbridge.application.file_upload import upload_files, wire_records
from transbridge.application.io.dialogue_order_projection import project_dialogue_order
from transbridge.application.io.paratranz_classification import classify_entries
from transbridge.application.io.paratranz_context_order import parse_ordered_context
from transbridge.application.io.paratranz_mapping import map_paratranz_records
from transbridge.application.sync.candidate_matching import OriginalContentIndex
from transbridge.application.sync.models import LocalEntrySnapshot, RemoteEntrySnapshot
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.converter.translation_entry_collection_export import export_to_categorized_json_files
from transbridge.parser.plugin.dialogue_order_evidence import read_dialogue_order_evidence
from transbridge.parser.plugin_parser import PluginParser


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "fixture.esp"
    path.write_bytes(dialogue_bytes())
    parser = PluginParser()
    entries = tuple(parser.parse_plugin(path, skip_empty=False, raise_on_error=True))
    evidence = read_dialogue_order_evidence(parser.get_plugin())
    assert evidence.complete and not evidence.invalid_quests
    return entries, evidence


def dialogue(groups):
    return next(group for group in groups if group.category_id == "dialogue:00000001")


def test_real_plugin_projection_keeps_info_order_and_source_matching_contract(source):
    entries, evidence = source
    before = [entry.to_dict() for entry in entries]
    result = dialogue(classify_entries(entries, dialogue_order=evidence))
    assert [entry.original for entry in result.entries] == [
        "First topic",
        "First reply",
        "Second reply",
        "Later topic",
        "Later reply",
    ]
    originals = {entry.identity: entry for entry in entries}
    for entry in result.entries:
        original = originals[entry.identity]
        assert (entry.key, entry.original, entry.translation, entry.stage) == (
            original.key,
            original.original,
            original.translation,
            original.stage,
        )
        assert dict(entry.metadata)["plugin.source_order"] == dict(original.metadata)["plugin.source_order"]
        assert parse_ordered_context(entry.context)[0] == original.context
    assert [entry.to_dict() for entry in entries] == before


def test_subset_uses_complete_numbers_and_current_translations(source):
    entries, evidence = source
    full = categorize_sources((UploadSource("source", "fixture.esp", entries, evidence),))
    wanted = next(entry for entry in entries if entry.original == "Later reply")
    actual = replace(wanted, translation="最新译文", stage=3)
    partial = categorize_sources((UploadSource("source", "fixture.esp", (actual,), evidence, entries),))
    expected = next(row for group in full for row in wire_records(group) if row["original"] == wanted.original)
    (row,) = wire_records(partial[0])
    assert (row["key"], row["context"]) == (expected["key"], expected["context"])
    assert (row["translation"], row["stage"]) == ("最新译文", 3)
    assert partial[0].entries[0].identity == wanted.identity


def test_plain_upload_ignores_dialogue_evidence(source):
    entries, evidence = source
    old = upload_files((UploadSource("source", "fixture.esp", entries),), categorized=False)
    new = upload_files((UploadSource("source", "fixture.esp", entries, evidence),), categorized=False)
    assert old == new


def test_writer_and_wire_share_frozen_numbers_and_shards(source, tmp_path):
    entries, evidence = source
    files = categorize_sources((UploadSource("source", "fixture.esp", entries, evidence),))
    file = dialogue(files)
    export_to_categorized_json_files(TranslationEntryCollection(entries), tmp_path, dialogue_order=evidence)
    exported = json.loads((tmp_path / "任务/对话_[Fixture Quest].json").read_text(encoding="utf-8"))
    assert exported == list(wire_records(file))
    shards = (replace(file, entries=file.entries[:2]), replace(file, entries=file.entries[2:]))
    assert [row for shard in shards for row in wire_records(shard)] == exported


def test_download_matches_after_reordering_and_display_prefix_change(source):
    entries, evidence = source
    file = dialogue(categorize_sources((UploadSource("source", "fixture.esp", entries, evidence),)))
    rows = [{**row, "translation": f"译文:{row['original']}"} for row in reversed(wire_records(file))]
    mapped = map_paratranz_records(rows, entries[0].identity.namespace)
    assert not mapped.diagnostics
    converted = [item.to_translation_entry() for item in mapped.entries]
    remote = tuple(
        RemoteEntrySnapshot(item.identity, "remote", item.original, item.translation, item.context or "", stage=1)
        for item in converted
    )
    matcher = OriginalContentIndex(remote, order_key=lambda item: item.entry_key.serialize())
    for original in entries:
        if not original.context.startswith(("DIAL:", "INFO:")):
            continue
        local = LocalEntrySnapshot(original.identity, original.revision, original.original, "", original.context)
        matched, _, success = matcher.match(local)
        assert success and matched.entry_key == original.identity
        assert matched.translation == f"译文:{original.original}"


def test_invalid_graph_falls_back_without_losing_entries(source, caplog):
    entries, evidence = source
    bad = replace(evidence, invalid_quests=("00000001",))
    old = classify_entries(entries)
    assert classify_entries(entries, dialogue_order=bad) == old
    assert "DIALOGUE_ORDER_FALLBACK" in caplog.text


def test_bad_branch_preferences_force_a_but_keep_real_links(source):
    entries, evidence = source
    reports = {}
    output, applied = project_dialogue_order(
        entries, replace(evidence, invalid_branch_quests=("00000001",)), reports=reports
    )
    assert applied and output
    assert reports["00000001"]["algorithm"]["mode"] == "pure_A_weak_component_contiguous"
    assert not reports["00000001"]["edges_unsatisfied"]


def test_complete_source_rejects_duplicate_identity(source):
    entries, evidence = source
    with pytest.raises(ValueError, match="重复"):
        categorize_sources((UploadSource("source", "fixture.esp", entries, evidence, (*entries, entries[0])),))


def test_script_export_filters_empty_text_after_complete_numbering(source, tmp_path):
    entries, evidence = source
    complete = tuple(replace(entry, original="") if entry.original == "First reply" else entry for entry in entries)
    selected = tuple(entry for entry in complete if entry.original)
    export_to_categorized_json_files(TranslationEntryCollection(complete), tmp_path / "all", dialogue_order=evidence)
    export_to_categorized_json_files(
        TranslationEntryCollection(selected),
        tmp_path / "nonempty",
        dialogue_order=evidence,
        classification_entries=complete,
    )
    relative = "任务/对话_[Fixture Quest].json"
    full = json.loads((tmp_path / "all" / relative).read_text(encoding="utf-8"))
    filtered = json.loads((tmp_path / "nonempty" / relative).read_text(encoding="utf-8"))
    assert filtered == [row for row in full if row["original"]]
