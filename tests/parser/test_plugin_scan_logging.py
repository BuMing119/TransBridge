"""Plugin scans retain diagnostics without emitting a record for every string."""

import logging

import pytest
from sse_plugin_interface.group import Group

from tests.parser.test_plugin_parser import FakePluginString
from tests.parser.test_plugin_tree_order import _group, _string_record
from transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext
from transbridge.parser.plugin_parser import PluginParser
from transbridge.parser.strings_file import PluginStringsLookup


@pytest.mark.parametrize("count", [1, 100])
def test_entry_conversion_aggregates_dialogue_fill_and_empty_counts_without_changing_entries(caplog, count):
    strings = [FakePluginString("Topic", "000000A0|scan.esp", "DIAL FULL", "Topic text")]
    strings.extend(
        FakePluginString(
            None,
            f"{0x1000 + index:08X}|scan.esp",
            "INFO NAM1",
            f"Line {index}",
            dialogue_topic="000000A0|scan.esp",
        )
        for index in range(count)
    )
    strings.extend(
        FakePluginString(f"Empty{index}", f"{0x2000 + index:08X}|scan.esp", "BOOK DESC", "") for index in range(count)
    )
    progress = []
    with caplog.at_level(logging.DEBUG, logger="PluginParser"):
        entries = PluginParser().create_entries(strings, progress_callback=lambda *args: progress.append(args))

    assert [entry.original for entry in entries] == ["Topic text", *(f"Line {index}" for index in range(count))]
    assert all(entry.id.startswith("Topic:") for entry in entries)
    assert [dict(entry.metadata)["plugin.source_order"] for entry in entries] == list(range(count + 1))
    assert len(progress) == 2 * count + 1
    assert progress[-1][:2] == (2 * count + 1, 2 * count + 1)
    records = [record for record in caplog.records if record.name == "PluginParser"]
    assert len(records) <= 6
    filled = [record for record in records if "Filled INFO" in record.getMessage()]
    assert len(filled) == 1
    assert filled[0].args == (count,)
    skipped = [record for record in records if "empty strings" in record.getMessage()]
    assert len(skipped) == 1 and skipped[0].args[0] == count
    assert not any(record.getMessage().startswith("Mapped DIAL ") for record in records)


def _missing_plugin(count=120, *, identifier_type=int):
    missing = []
    for index in range(count):
        record = _string_record("INFO", f"{0x1000 + index:08X}", "NAM1", "placeholder")
        record.subrecords[0].string = identifier_type(100 + index)
        missing.append(record)
    root = _group(
        "DIAL",
        Group.GroupType.Normal,
        [
            _string_record("DIAL", "000000A0", "FULL", "Topic text"),
            _group("000000A0", Group.GroupType.TopicChildren, missing[: count // 2]),
            _group("000000A0", Group.GroupType.TopicChildren, missing[count // 2 :]),
        ],
    )
    resolved = _string_record("BOOK", "00003000", "DESC", "placeholder")
    resolved.subrecords[0].string = 7
    plugin = SSEPluginWithContext("scan.esp")
    plugin._SSEPlugin__masters = []
    plugin._SSEPlugin__groups = [root, _group("BOOK", Group.GroupType.Normal, [resolved])]
    return plugin, PluginStringsLookup({7: "Resolved text"})


@pytest.mark.parametrize("entrypoint", ["whole-plugin", "group-pairs", "group-dictionary"])
def test_nested_missing_strings_emit_one_bounded_locatable_scan_summary(caplog, entrypoint):
    plugin, lookup = _missing_plugin()
    with caplog.at_level(logging.DEBUG, logger="SSEPluginWithContext"):
        if entrypoint == "whole-plugin":
            pairs = plugin.extract_string_pairs_with_context(strings_lookup=lookup)
        elif entrypoint == "group-pairs":
            pairs = plugin.extract_group_string_pairs(plugin.groups[0], strings_lookup=lookup)
        else:
            pairs = list(plugin.extract_group_strings_with_context(plugin.groups[0], strings_lookup=lookup).items())

    assert [item.string for item, _field in pairs] == (
        ["Topic text", "Resolved text"] if entrypoint == "whole-plugin" else ["Topic text"]
    )
    records = [record for record in caplog.records if record.name == "SSEPluginWithContext"]
    summaries = [record for record in records if "Unresolved" in record.getMessage()]
    assert len(summaries) == 1
    assert summaries[0].args[:2] == ("scan.esp", 120)
    samples = summaries[0].args[2]
    assert samples.count(" in INFO NAM1 (") == 5
    assert "0x00000064 in INFO NAM1 (00001000|scan.esp)" in samples
    assert "0x000000db" not in samples
    assert len(records) <= 2


def test_missing_string_counts_reset_for_each_scan(caplog):
    plugin, lookup = _missing_plugin(40)
    with caplog.at_level(logging.DEBUG, logger="SSEPluginWithContext"):
        first = plugin.extract_string_pairs_with_context(strings_lookup=lookup)
        second = plugin.extract_string_pairs_with_context(strings_lookup=lookup)

    assert [(item.form_id, item.string) for item, _field in first] == [
        (item.form_id, item.string) for item, _field in second
    ]
    summaries = [
        record
        for record in caplog.records
        if record.name == "SSEPluginWithContext" and "Unresolved" in record.getMessage()
    ]
    assert [record.args[1] for record in summaries] == [40, 40]


class _UnformattableIdentifier(int):
    def __format__(self, spec):
        raise AssertionError("Disabled DEBUG diagnostics must not format unresolved string IDs")


def test_disabled_debug_does_not_format_missing_id_diagnostics(caplog):
    plugin, lookup = _missing_plugin(40, identifier_type=_UnformattableIdentifier)

    with caplog.at_level(logging.INFO, logger="SSEPluginWithContext"):
        pairs = plugin.extract_string_pairs_with_context(strings_lookup=lookup)

    assert [item.string for item, _field in pairs] == ["Topic text", "Resolved text"]
    assert not any(record.name == "SSEPluginWithContext" for record in caplog.records)
