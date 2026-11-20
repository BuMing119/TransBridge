# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Build dialogue projections with one cached, read-only catalog of the loaded source."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from io import BytesIO
from pathlib import Path
from threading import Lock

from transbridge.application.io.contracts import SourceSnapshot
from transbridge.application.io.dialogue_reading_view import ReadingEntry, build_dialogue_reading_view
from transbridge.application.io.plugin_dialogue_catalog import dialogue_catalog_from_snapshot
from transbridge.application.io.plugin_dialogue_order import dialogue_order_from_snapshot
from transbridge.application.io.text_source_options import snapshot_read_options
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.parser.plugin.dialogue_catalog import DialogueCatalog, DialogueRecord, read_dialogue_catalog
from transbridge.parser.plugin.dialogue_order_binary import check_cancelled
from transbridge.parser.plugin.dialogue_order_evidence import read_dialogue_order_evidence
from transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext

from .index import DialogueIndex, build_dialogue_index


def _snapshot_directory(snapshot, cancellation):
    evidence = dialogue_order_from_snapshot(snapshot, cancellation=cancellation)
    if snapshot.content is not None:
        parsed = SSEPluginWithContext.from_stream(
            BytesIO(snapshot.content), Path(snapshot.source.uri).name, **snapshot_read_options(snapshot)
        )
        return read_dialogue_catalog(parsed), evidence
    catalog = dialogue_catalog_from_snapshot(snapshot, cancellation=cancellation)
    if catalog is not None:
        return catalog, evidence
    # Older projects retained DIAL ownership and order but no SCEN facts.
    records = () if evidence is None else evidence.records
    catalog = DialogueCatalog(
        tuple(
            DialogueRecord(record.kind, record.form_id, quest_id=record.quest_id)
            for record in records
            if record.kind == "DIAL" and record.valid and not record.deleted
        )
    )
    return catalog, evidence


class DialogueIndexLoader:
    """Called in a worker; translation edits reuse the source's immutable catalog."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._source = None
        self._catalog = DialogueCatalog()
        self._evidence = None

    def build(
        self,
        entries: Iterable[TranslationEntry],
        *,
        plugin: SSEPluginWithContext | None = None,
        snapshot: SourceSnapshot | None = None,
        load_snapshot: Callable[[], SourceSnapshot] | None = None,
        on_preview: Callable[[DialogueIndex], None] | None = None,
        cancellation=None,
        reading_entries: Iterable[ReadingEntry] | None = None,
    ) -> DialogueIndex:
        cancelled = (lambda: cancellation.is_cancelled) if cancellation is not None else None
        check_cancelled(cancelled)
        entries = tuple(entries)
        # A live parser can supply a catalog for an older metadata-only snapshot.
        # A paged project instead loads its format fields lazily using the slot
        # descriptor as the stable cache identity.
        source = plugin if plugin is not None and (snapshot is None or snapshot.content is None) else snapshot
        with self._lock:
            check_cancelled(cancelled)
            if source is not self._source:
                if on_preview is not None:
                    # Hydrated entry relationships already support task browsing.
                    # Publish them before parsing a potentially large source for scenes.
                    on_preview(build_dialogue_index(entries))
                if isinstance(source, SourceSnapshot):
                    captured = load_snapshot() if load_snapshot is not None else source
                    catalog, evidence = _snapshot_directory(captured, cancellation)
                else:
                    catalog = DialogueCatalog() if source is None else read_dialogue_catalog(source)
                    evidence = (
                        None if source is None else read_dialogue_order_evidence(source, cancellation=cancellation)
                    )
                check_cancelled(cancelled)
                self._source, self._catalog, self._evidence = source, catalog, evidence
            catalog, evidence = self._catalog, self._evidence
        view = build_dialogue_reading_view(
            (
                tuple(ReadingEntry(entry.identity, entry.context, entry.metadata) for entry in entries)
                if reading_entries is None
                else tuple(reading_entries)
            ),
            evidence,
            cancellation=cancelled,
        )
        check_cancelled(cancelled)
        topic_orders = {(namespace.value, quest): order for (namespace, quest), order in view.topic_orders.items()}
        return build_dialogue_index(entries, catalog=catalog, topic_orders=topic_orders)
