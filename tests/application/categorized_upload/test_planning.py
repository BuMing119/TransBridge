"""Shared classification preserves entries, identities and source order."""

from transbridge.application.categorized_upload import UploadSource, categorize_sources
from transbridge.application.io.identity import EntryKey, ExternalEntryRef, SourceNamespace
from transbridge.converter.translation_entry import TranslationEntry


def entry(key="same", original="Original", context="NPC_:FULL", *, source="a", ref=None, qualified=False):
    return TranslationEntry(
        key,
        key,
        original,
        "译文",
        1,
        context,
        entry_key=EntryKey(SourceNamespace(source), key, original if qualified else None),
        external_refs=() if ref is None else (ExternalEntryRef("paratranz", "project:7", ref),),
    )


def test_classification_preserves_every_entry_unknown_context_and_global_order():
    entries = (
        entry("name"),
        entry("unknown", context="CUSTOM"),
        entry("dial", context="INFO:NAM1|00000010"),
        entry("book", context="BOOK:FULL|unrelated"),
    )
    files = categorize_sources((UploadSource("a", "Plugin.esp", entries),))
    assert {file.path for file in files} == {
        "Plugin.esp/人名.json",
        "Plugin.esp/其他.json",
        "Plugin.esp/任务/对话_[00000010].json",
        "Plugin.esp/书籍_书名.json",
    }
    projected = {item.key: item for file in files for item in file.entries}
    assert len(projected) == 4
    assert projected["dial"].context == "00000002|INFO:NAM1|00000010"
    assert projected["unknown"].context == "CUSTOM"
    assert [item.context for item in entries] == ["NPC_:FULL", "CUSTOM", "INFO:NAM1|00000010", "BOOK:FULL|unrelated"]


def test_task_directory_keeps_quest_dialogues_separate_and_journal_together():
    entries = (
        entry("name", context="QUST:FULL"),
        entry("objective", context="QUST:NNAM"),
        entry("journal", context="QUST:CNAM"),
        entry("line", context="INFO:NAM1|00000010"),
        entry("topic", context="DIAL:FULL|00000010"),
        entry("another", context="INFO:NAM1|00000020"),
        entry("unbound", context="INFO:NAM1"),
        entry("unbound-topic", context="DIAL:FULL|"),
    )
    source = UploadSource("a", "A.esp", entries)
    files = categorize_sources((source,))
    assert {file.path: {item.key for item in file.entries} for file in files} == {
        "A.esp/任务/任务日志.json": {"name", "objective", "journal"},
        "A.esp/任务/对话_[00000010].json": {"line", "topic"},
        "A.esp/任务/对话_[00000020].json": {"another"},
        "A.esp/任务/对话_[未关联任务].json": {"unbound", "unbound-topic"},
    }
    assert {file.category_id for file in files} == {
        "任务日志",
        "dialogue:00000010",
        "dialogue:00000020",
        "dialogue:未关联任务",
    }


def test_same_plugin_labels_disambiguate_stably_without_rewriting_keys():
    sources = (UploadSource("a", "Same.esp", (entry(),)), UploadSource("b", "same.esp", (entry(source="b"),)))
    first = categorize_sources(sources)
    second = categorize_sources(tuple(reversed(sources)))
    assert {file.source_id: file.path for file in first} == {file.source_id: file.path for file in second}
    assert len({file.path.casefold() for file in first}) == 2
    assert all(".esp_" in file.path for file in first)
    assert all(file.entries[0].key == "same" for file in first)
