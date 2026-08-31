from io import BytesIO

from tests.dialogue_catalog_support import dialogue_plugin, field, plugin_bytes, record, reference
from transbridge.parser.plugin.dialogue_catalog import read_dialogue_catalog
from transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext


def test_catalog_reads_real_record_identifiers_and_explicit_scene_actions():
    plugin = dialogue_plugin()
    catalog = read_dialogue_catalog(plugin)
    records = {record.form_id: record for record in catalog.records}
    assert records["00000001"].editor_id == "Quest"
    assert records["00000010"].editor_id == "Topic10"
    assert records["00000011"].editor_id == ""
    assert records["00000011"].category == "Scene"
    assert records["00000012"].editor_id == "TestScene"
    assert records["00000012"].quest_id == "00000001"
    assert records["00000012"].topic_ids == ("00000010", "00000011")
    assert records["00000013"].topic_ids == ()
    assert read_dialogue_catalog(plugin) == catalog


def test_branch_quest_fallback_and_start_topic_do_not_depend_on_record_order():
    content = plugin_bytes({
        "DIAL": record("DIAL", 0x10, reference("BNAM", 0x20)) + record("DIAL", 0x11, b""),
        "DLBR": record("DLBR", 0x20, reference("QNAM", 1) + reference("SNAM", 0x11)),
    })
    catalog = read_dialogue_catalog(SSEPluginWithContext.from_stream(BytesIO(content), "fixture.esp"))
    assert [(item.form_id, item.quest_id) for item in catalog.records] == [
        ("00000010", "00000001"),
        ("00000011", "00000001"),
    ]


def test_malformed_action_and_null_references_do_not_fabricate_scene_links():
    content = plugin_bytes({
        "SCEN": record(
            "SCEN",
            0x12,
            reference("DATA", 0x99)  # Not inside a dialogue action.
            + field("ANAM", b"\0\0")
            + reference("DATA", 0)
            + field("DATA", b"\1")
            + field("ANAM", b"")
            + field("ANAM", b"\0")
            + reference("PNAM", 0xBAD)
            + reference("DATA", 0x98),
        )
    })
    scene = read_dialogue_catalog(SSEPluginWithContext.from_stream(BytesIO(content), "fixture.esp")).records[0]
    assert scene.quest_id == ""
    assert scene.topic_ids == ()
