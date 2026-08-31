from sse_plugin_interface.datatypes import RawString
from sse_plugin_interface.group import Group
from sse_plugin_interface.record import Record
from sse_plugin_interface.subrecord import StringSubrecord

from transbridge.parser.plugin.item import InfoContext
from transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext


def _string_record(record_type: str, formid: str, field_type: str, text: str) -> Record:
    field = StringSubrecord(field_type)
    field.string = RawString.from_str(text, "utf8")
    field.index = 0

    record = Record()
    record.type = record_type
    record.formid = formid
    record.subrecords = [field]
    return record


def _group(label: str, group_type: Group.GroupType, children: list[Group | Record]) -> Group:
    group = Group()
    group.label = label
    group.group_type = group_type
    group.children = children
    return group


def test_dialogue_strings_follow_physical_group_tree_instead_of_formid_order() -> None:
    dial_a = _string_record("DIAL", "000000A0", "FULL", "Topic A")
    info_a = _string_record("INFO", "000000F0", "NAM1", "Line A")
    dial_b = _string_record("DIAL", "000000B0", "FULL", "Topic B")
    info_b = _string_record("INFO", "00000010", "NAM1", "Line B")
    root = _group(
        "DIAL",
        Group.GroupType.Normal,
        [
            dial_a,
            _group("000000A0", Group.GroupType.TopicChildren, [info_a]),
            dial_b,
            _group("000000B0", Group.GroupType.TopicChildren, [info_b]),
        ],
    )

    plugin = SSEPluginWithContext("fixture.esp")
    plugin._SSEPlugin__masters = []
    plugin._SSEPlugin__groups = [root]

    result = plugin.extract_strings_with_context()

    assert [(item.type, item.string) for item in result] == [
        ("DIAL FULL", "Topic A"),
        ("INFO NAM1", "Line A"),
        ("DIAL FULL", "Topic B"),
        ("INFO NAM1", "Line B"),
    ]
    assert [item.form_id for item in result] == [
        "000000A0|fixture.esp",
        "000000F0|fixture.esp",
        "000000B0|fixture.esp",
        "00000010|fixture.esp",
    ]
    assert isinstance(result[1].context, InfoContext)
    assert result[1].context.dialogue_topic == "000000A0|fixture.esp"
    assert isinstance(result[3].context, InfoContext)
    assert result[3].context.dialogue_topic == "000000B0|fixture.esp"
