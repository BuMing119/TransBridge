# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Carry complete plugin navigation facts through input-independent projects."""

from __future__ import annotations

from io import BytesIO
from pathlib import PureWindowsPath

from transbridge.parser.plugin.dialogue_catalog import (
    CATALOG_VERSION,
    SUPPORTED_CATALOG_VERSIONS,
    DialogueCatalog,
    catalog_from_payload,
    catalog_payload,
    read_dialogue_catalog,
)
from transbridge.parser.plugin.dialogue_order_binary import check_cancelled
from transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext

from .contracts import FormatId, SourceSnapshot
from .text_source_options import snapshot_read_options

METADATA_KEY = "plugin_dialogue_catalog"


def capture_dialogue_catalog_metadata(plugin, sha256: str, *, cancellation=None) -> tuple[str, tuple]:
    """Capture labels, stringless topics and scene links while the parser exists."""
    catalog = read_dialogue_catalog(plugin, cancellation=cancellation)
    payload = catalog_payload(catalog)
    return METADATA_KEY, (CATALOG_VERSION, sha256, payload)


def dialogue_catalog_from_snapshot(snapshot: SourceSnapshot, *, cancellation=None) -> DialogueCatalog | None:
    """Read saved catalog facts; missing legacy facts never cause filesystem reads.

    A caller with captured bytes may reconstruct an older snapshot separately.
    A project with neither saved catalog facts nor bytes retains entry-based
    navigation; its historical reference path is never an input authority.
    """
    cancelled = (lambda: cancellation.is_cancelled) if cancellation is not None else None
    check_cancelled(cancelled)
    if snapshot.format_id is not FormatId.PLUGIN_SSE:
        return None
    metadata = dict(snapshot.metadata)
    if METADATA_KEY not in metadata:
        return None
    value = metadata[METADATA_KEY]
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError("Invalid saved dialogue catalog envelope")
    version, digest, payload = value
    if type(version) is not int or version not in SUPPORTED_CATALOG_VERSIONS or digest != snapshot.sha256:
        raise ValueError("Saved dialogue catalog version or source revision differs")
    if version < CATALOG_VERSION and snapshot.content is not None:
        plugin = SSEPluginWithContext.from_stream(
            BytesIO(snapshot.content), PureWindowsPath(snapshot.source.uri).name, **snapshot_read_options(snapshot)
        )
        return read_dialogue_catalog(plugin, cancellation=cancellation)
    catalog = catalog_from_payload(payload)
    check_cancelled(cancelled)
    return catalog
