from dataclasses import replace

import pytest

from tests.dialogue_catalog_support import dialogue_plugin, dialogue_plugin_bytes
from tests.dialogue_support import dialogue_entries
from transbridge.application.dialogue import loading
from transbridge.application.io.contracts import FormatId, SourceDescriptor, SourceSnapshot


def snapshot(scene_name="TestScene"):
    return SourceSnapshot.from_bytes(
        SourceDescriptor("nonexistent/fixture.esp"), FormatId.PLUGIN_SSE, dialogue_plugin_bytes(scene_name)
    )


@pytest.mark.parametrize("source_kind", ["plugin", "snapshot"])
def test_source_catalog_is_cached_across_translation_changes_and_replaced_on_source_change(monkeypatch, source_kind):
    calls = []
    read = loading.read_dialogue_catalog

    def counted(plugin):
        calls.append(plugin)
        return read(plugin)

    monkeypatch.setattr(loading, "read_dialogue_catalog", counted)
    loader = loading.DialogueIndexLoader()
    source = dialogue_plugin() if source_kind == "plugin" else snapshot()
    first = loader.build(dialogue_entries(), **{source_kind: source})
    changed = [replace(entry, translation="新译文") for entry in dialogue_entries()]
    assert loader.build(changed, **{source_kind: source}) == first
    assert len(calls) == 1
    loader.build(changed, **{source_kind: dialogue_plugin() if source_kind == "plugin" else snapshot("ChangedScene")})
    assert len(calls) == 2
    assert all(topic.kind != "SCEN" for quest in loader.build(changed).quests for topic in quest.topics)


def test_snapshot_hydration_reads_captured_bytes_without_opening_the_source_path():
    entries = [replace(entry, form_id_with_plugin=None, editor_id="") for entry in dialogue_entries()]
    result = loading.DialogueIndexLoader().build(entries, snapshot=snapshot())
    assert result.quests[0].label == "QUST {Quest} [00000001]"
    assert any(topic.label == "SCEN {TestScene} [00000012]" for topic in result.quests[0].topics)


def test_broken_snapshot_reports_parser_failure_and_allows_a_later_valid_source():
    source = SourceSnapshot.from_bytes(SourceDescriptor("broken.esp"), FormatId.PLUGIN_SSE, b"invalid plugin")
    loader = loading.DialogueIndexLoader()
    with pytest.raises(Exception):
        loader.build(dialogue_entries(), snapshot=source)
    assert loader.build(dialogue_entries(), snapshot=snapshot()).quests
