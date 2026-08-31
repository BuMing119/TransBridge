"""Build dialogue projections with one cached, read-only catalog of the loaded source."""

from __future__ import annotations

from collections.abc import Iterable
from io import BytesIO
from pathlib import Path
from threading import Lock

from transbridge.application.io.contracts import SourceSnapshot
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.parser.plugin.dialogue_catalog import DialogueCatalog, read_dialogue_catalog
from transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext

from .index import DialogueIndex, build_dialogue_index


class DialogueIndexLoader:
    """Called in a worker; translation edits reuse the source's immutable catalog."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._source = None
        self._catalog = DialogueCatalog()

    def build(
        self,
        entries: Iterable[TranslationEntry],
        *,
        plugin: SSEPluginWithContext | None = None,
        snapshot: SourceSnapshot | None = None,
    ) -> DialogueIndex:
        source = snapshot if snapshot is not None and snapshot.content is not None else plugin
        with self._lock:
            if source is not self._source:
                if isinstance(source, SourceSnapshot):
                    # Project hydration has no live parser. Read its captured bytes,
                    # never a possibly changed plugin file on disk.
                    parsed = SSEPluginWithContext.from_stream(BytesIO(source.content), Path(source.source.uri).name)
                    catalog = read_dialogue_catalog(parsed)
                else:
                    catalog = DialogueCatalog() if source is None else read_dialogue_catalog(source)
                self._source, self._catalog = source, catalog
            catalog = self._catalog
        return build_dialogue_index(entries, catalog=catalog)
