from io import BytesIO

import pytest

from tests.dialogue_catalog_support import field, plugin_bytes, record, reference
from transbridge.parser.plugin.dialogue_catalog import read_dialogue_catalog
from transbridge.parser.plugin.dialogue_order_evidence import read_dialogue_order_bytes, read_dialogue_order_evidence
from transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext


def _dialogue(topic, interval):
    return field("ANAM", b"\0\0") + reference("DATA", topic) + interval + field("ANAM", b"")


def _interval(start, end):
    return reference("SNAM", start) + reference("ENAM", end)


def _assert_order(actions, expected):
    content = plugin_bytes({"SCEN": record("SCEN", 1, actions + reference("PNAM", 2))})
    plugin = SSEPluginWithContext.from_stream(BytesIO(content), "phase.esp")
    assert read_dialogue_catalog(plugin).records[0].topic_ids == expected
    assert read_dialogue_order_evidence(plugin).scenes[0].topic_ids == expected
    assert read_dialogue_order_bytes(content).scenes[0].topic_ids == expected


def test_phase_order_handles_overlap_ties_reused_topics_and_non_dialogue_actions():
    actions = (
        _dialogue(0x30, _interval(6, 6))
        + _dialogue(0x20, _interval(4, 9))
        + field("ANAM", b"\x02\0")
        + field("SNAM", b"\0\0\x80\x3f")
        + reference("DATA", 0x99)
        + field("ANAM", b"")
        + _dialogue(0x10, _interval(4, 4))
        + _dialogue(0x30, _interval(0, 3))
        + _dialogue(0x40, _interval(2, 2))
    )
    _assert_order(actions, ("00000030", "00000040", "00000020", "00000010"))


@pytest.mark.parametrize(
    "unreliable",
    [
        b"",
        reference("ENAM", 3),
        reference("SNAM", 1),
        field("SNAM", b"\x01") + reference("ENAM", 1),
        _interval(5, 4),
        _interval(0xFFFFFFFF, 0xFFFFFFFF),
        _interval(1, 2) + reference("SNAM", 2),
    ],
)
def test_incomplete_scene_timing_keeps_its_physical_topic_order(unreliable):
    actions = _dialogue(3, _interval(4, 4)) + _dialogue(1, unreliable) + _dialogue(2, _interval(0, 1))
    _assert_order(actions, ("00000003", "00000001", "00000002"))


def test_end_phase_outside_the_declared_phases_keeps_physical_order():
    two_phases = field("HNAM", b"") * 4
    actions = _dialogue(3, _interval(1, 2)) + _dialogue(1, _interval(0, 0))
    _assert_order(two_phases + actions, ("00000003", "00000001"))


def test_scene_with_a_missing_interval_does_not_affect_other_scenes():
    first = _dialogue(3, _interval(4, 4)) + _dialogue(2, b"")
    second = _dialogue(5, _interval(2, 2)) + _dialogue(4, _interval(1, 1))
    content = plugin_bytes({"SCEN": record("SCEN", 10, first) + record("SCEN", 8, second)})
    scenes = read_dialogue_order_bytes(content).scenes
    assert [(scene.form_id, scene.topic_ids) for scene in scenes] == [
        ("0000000A", ("00000003", "00000002")),
        ("00000008", ("00000004", "00000005")),
    ]
