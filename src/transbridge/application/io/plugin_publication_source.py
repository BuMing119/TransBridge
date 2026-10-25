"""Reuse a confirmed plugin's parsed source only within one publication."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.parser.plugin_parser import PluginParser
from transbridge.writer.plugin_source import capture_plugin_write_source

from .contracts import SourceSnapshot


@dataclass(slots=True)
class PreparedPluginSource:
    snapshot: SourceSnapshot
    parser: PluginParser | None
    entries: tuple[TranslationEntry, ...]
    physical_entries: tuple[TranslationEntry, ...]


@dataclass(slots=True)
class _PublicationSources:
    sources: dict[tuple[int, str], PreparedPluginSource] = field(default_factory=dict)
    rendered: set[tuple[int, str]] = field(default_factory=set)


_publication: ContextVar[_PublicationSources | None] = ContextVar("plugin_publication_sources", default=None)


@contextmanager
def plugin_source_scope():
    """Each call/retry owns its sources; nesting and concurrent writes are isolated."""
    token = _publication.set(_PublicationSources())
    try:
        yield
    finally:
        _publication.reset(token)


def retire_plugin_parsers() -> None:
    """Keep original positions without overlapping two large decoded plugins."""
    publication = _publication.get()
    if publication is not None:
        for prepared in publication.sources.values():
            prepared.parser = None


def prepared_plugin_source(snapshot: SourceSnapshot, options: dict, *, for_write: bool = False) -> PreparedPluginSource:
    from .plugin_write import _parser_from_snapshot

    language = str(dict(snapshot.metadata).get("localized_language", options.get("language", "english")))
    key = (id(snapshot), language)
    publication = _publication.get()
    if publication is not None and for_write:
        # A renderer may retry within the same publication. Never hand it an
        # already-mutated library instance, even when its inputs are unchanged.
        cached = publication.sources.get(key)
        if key in publication.rendered or (cached is not None and cached.parser is None):
            publication.sources.pop(key, None)
        publication.rendered.add(key)
    if publication is not None and key in publication.sources:
        return publication.sources[key]
    parser, entries = _parser_from_snapshot(snapshot, options)
    plugin = parser.get_plugin()
    if plugin is None:
        raise ValueError("plugin parser did not produce a source plugin")
    # Capture all physical positions, including conflict-skipped and empty ones,
    # before the writer can mutate inline subrecords of this library instance.
    physical = tuple(entry for entry, _subrecord in capture_plugin_write_source(plugin, parser.get_strings_lookup()))
    prepared = PreparedPluginSource(snapshot, parser, tuple(entries), physical)
    if publication is not None:
        publication.sources[key] = prepared
    return prepared
