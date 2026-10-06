"""Retain source-text locators while a legacy plugin instance is edited."""

from copy import copy
from dataclasses import dataclass, field
from weakref import WeakKeyDictionary

from sse_plugin_interface.datatypes import RawString
from sse_plugin_interface.subrecord import StringSubrecord

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext
from transbridge.parser.plugin_parser import PluginParser
from transbridge.parser.strings_file import PluginStringsLookup


@dataclass
class _PluginSources:
    inline_originals: dict[StringSubrecord, str] = field(default_factory=dict)
    entries: dict[PluginStringsLookup | None, tuple[tuple[TranslationEntry, StringSubrecord], ...]] = field(
        default_factory=dict
    )


_sources: WeakKeyDictionary[SSEPluginWithContext, _PluginSources] = WeakKeyDictionary()


def capture_plugin_write_source(
    plugin: SSEPluginWithContext,
    strings_lookup: PluginStringsLookup | None,
) -> tuple[tuple[TranslationEntry, StringSubrecord], ...]:
    """Share original physical locators across writers without retaining closed plugins."""
    captured = _sources.setdefault(plugin, _PluginSources())
    if strings_lookup not in captured.entries:
        pairs = plugin.extract_string_pairs_with_context(strings_lookup=strings_lookup)
        originals = []
        for ps, subrecord in pairs:
            source = copy(ps)
            if isinstance(subrecord.string, RawString):
                source.string = captured.inline_originals.setdefault(subrecord, ps.string)
            originals.append(source)
        entries = PluginParser().create_entries(originals, skip_empty=False)
        captured.entries[strings_lookup] = tuple(
            (entry, subrecord) for entry, (_, subrecord) in zip(entries, pairs, strict=True)
        )
    return captured.entries[strings_lookup]
