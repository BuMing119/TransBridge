"""Old scene metadata keeps its provenance until captured source is refreshed."""

from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pytest

from tests.dialogue_catalog_support import field, plugin_bytes, record, reference
from transbridge.application.dialogue.loading import DialogueIndexLoader
from transbridge.application.io import (
    FormatId,
    SourceDescriptor,
    SourceSnapshot,
    plugin_dialogue_catalog as catalogs,
    plugin_dialogue_order as orders,
)
from transbridge.application.io.dialogue_reading_view import build_dialogue_reading_view
from transbridge.parser.plugin.dialogue_catalog import catalog_payload, read_dialogue_catalog
from transbridge.parser.plugin.dialogue_order_evidence import evidence_payload, read_dialogue_order_bytes
from transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext
from transbridge.parser.plugin_parser import PluginParser

PHYSICAL = ("00000020", "00000010")
PHASE = tuple(reversed(PHYSICAL))


def phase_plugin_bytes():
    return plugin_bytes({
        "QUST": record("QUST", 1, field("EDID", b"Quest\0")),
        "DIAL": b"".join(
            record("DIAL", topic, reference("QNAM", 1) + field("FULL", text + b"\0"))
            for topic, text in ((0x20, b"Second"), (0x10, b"First"))
        ),
        "SCEN": record(
            "SCEN",
            0x30,
            reference("PNAM", 1)
            + b"".join(
                field("ANAM", b"\0\0")
                + reference("SNAM", phase)
                + reference("ENAM", phase)
                + reference("DATA", topic)
                + field("ANAM", b"")
                for phase, topic in ((2, 0x20), (1, 0x10))
            ),
        ),
    })


def _legacy_snapshot(version=2):
    source = SourceSnapshot.from_bytes(
        SourceDescriptor("Z:/removed/scene.esp"), FormatId.PLUGIN_SSE, phase_plugin_bytes()
    )
    plugin = SSEPluginWithContext.from_stream(BytesIO(source.content), "scene.esp")
    catalog = read_dialogue_catalog(plugin)
    catalog = replace(
        catalog,
        records=tuple(replace(item, topic_ids=PHYSICAL) if item.kind == "SCEN" else item for item in catalog.records),
    )
    evidence = read_dialogue_order_bytes(source.content)
    evidence = replace(evidence, scenes=tuple(replace(item, topic_ids=PHYSICAL) for item in evidence.scenes))
    payload = evidence_payload(evidence)
    return replace(
        source,
        metadata=(
            (orders.METADATA_KEY, (version, source.sha256, payload[:7] if version == 1 else payload)),
            (catalogs.METADATA_KEY, (1, source.sha256, catalog_payload(catalog))),
        ),
    )


@pytest.mark.parametrize("version", [1, 2])
def test_old_snapshot_bytes_refresh_both_navigation_and_reading_projection(version, monkeypatch):
    source = _legacy_snapshot(version)
    parser = PluginParser()
    entries = tuple(
        parser.parse_plugin(
            Path("scene.esp"), captured_content=source.content, discover_sibling_strings=False, raise_on_error=True
        )
    )
    # A legacy cache must never hide the phase order present in captured bytes.
    old = read_dialogue_order_bytes(source.content)
    old = replace(old, scenes=tuple(replace(item, topic_ids=PHYSICAL) for item in old.scenes))
    monkeypatch.setattr(orders, "_cache", orders.OrderedDict({(source.sha256, version): old}))
    monkeypatch.setattr(Path, "read_bytes", lambda *_: pytest.fail("Historical path must not be opened"))
    evidence = orders.dialogue_order_from_snapshot(source)
    assert evidence.extraction_version == 3
    assert evidence.scenes[0].topic_ids == PHASE
    assert (
        next(item for item in catalogs.dialogue_catalog_from_snapshot(source).records if item.kind == "SCEN").topic_ids
        == PHASE
    )
    view = build_dialogue_reading_view(entries, evidence)
    by_key = {entry.identity: entry.original for entry in entries}
    assert [by_key[key] for key in view.ordered_keys] == ["First", "Second"]
    index = DialogueIndexLoader().build(entries, snapshot=source)
    scene = next(topic for quest in index.quests for topic in quest.topics if topic.kind == "SCEN")
    assert [by_key[key] for key in scene.entries] == ["First", "Second"]


@pytest.mark.parametrize("version", [1, 2])
def test_metadata_only_legacy_does_not_claim_phase_order_or_reuse_current_cache(version, monkeypatch):
    source = _legacy_snapshot(version)
    current = read_dialogue_order_bytes(source.content)
    source = replace(source, content=None, lease_id="saved-source")
    monkeypatch.setattr(orders, "_cache", orders.OrderedDict({(source.sha256, 3): current}))
    monkeypatch.setattr(Path, "read_bytes", lambda *_: pytest.fail("Historical path must not be opened"))
    before = source.metadata
    evidence = orders.dialogue_order_from_snapshot(source)
    assert evidence.extraction_version == version
    assert evidence.scenes[0].topic_ids == PHYSICAL
    assert (
        next(item for item in catalogs.dialogue_catalog_from_snapshot(source).records if item.kind == "SCEN").topic_ids
        == PHYSICAL
    )
    assert source.metadata == before
