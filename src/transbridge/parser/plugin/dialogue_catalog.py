"""Read navigable quest, topic and scene records without creating translation entries."""

from __future__ import annotations

from dataclasses import dataclass

from sse_plugin_interface.group import Group
from sse_plugin_interface.record import Record

from .plugin_with_context import SSEPluginWithContext


@dataclass(frozen=True)
class DialogueRecord:
    kind: str
    form_id: str
    editor_id: str = ""
    quest_id: str = ""
    category: str = ""
    topic_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DialogueCatalog:
    records: tuple[DialogueRecord, ...] = ()


_CATEGORIES = ("Player", "Favor", "Scene", "Combat", "Favors", "Detection", "Service", "Miscellaneous")


def _records(groups):
    for item in groups:
        if isinstance(item, Group):
            yield from _records(item.children)
        elif isinstance(item, Record) and item.type in {"QUST", "DIAL", "SCEN", "DLBR"}:
            yield item


def _reference(data: bytes) -> str:
    value = int.from_bytes(data, "little") if len(data) == 4 else 0
    return f"{value:08X}" if value else ""


def _field_reference(record: Record, name: str) -> str:
    return next((_reference(field.data) for field in record.subrecords if field.type == name), "")


def _scene_links(record: Record) -> tuple[str, tuple[str, ...]]:
    quest_id = ""
    topics: dict[str, None] = {}
    in_action = False
    action_type = None
    for field in record.subrecords:
        if field.type == "ANAM":
            # A uint16 starts an action; an empty ANAM closes it. PNAM inside
            # a package action points to PACK, whereas outside it points to QUST.
            in_action = bool(field.data)
            action_type = int.from_bytes(field.data, "little") if len(field.data) == 2 else None
        elif field.type == "PNAM" and not in_action:
            quest_id = _reference(field.data)
        elif field.type == "DATA" and in_action and action_type == 0:
            topic_id = _reference(field.data)
            if topic_id:
                topics[topic_id] = None
    return quest_id, tuple(topics)


def read_dialogue_catalog(plugin: SSEPluginWithContext) -> DialogueCatalog:
    """Use explicit binary references only; record order never implies a link."""
    records = tuple(_records(plugin.groups))
    branches = {record.formid: _field_reference(record, "QNAM") for record in records if record.type == "DLBR"}
    starts = {
        _field_reference(record, "SNAM"): _field_reference(record, "QNAM")
        for record in records
        if record.type == "DLBR" and _field_reference(record, "SNAM")
    }
    result = []
    for record in records:
        if record.type == "DLBR":
            continue
        edid = plugin.get_record_edid(record)
        editor_id = str(edid) if edid is not None else ""
        quest_id, category, topics = "", "", ()
        if record.type == "DIAL":
            quest_id = (
                _field_reference(record, "QNAM")
                or branches.get(_field_reference(record, "BNAM"), "")
                or starts.get(record.formid, "")
            )
            data = next((field.data for field in record.subrecords if field.type == "DATA"), b"")
            if len(data) >= 2 and data[1] < len(_CATEGORIES):
                category = _CATEGORIES[data[1]]
        elif record.type == "SCEN":
            quest_id, topics = _scene_links(record)
        result.append(DialogueRecord(record.type, record.formid.upper(), editor_id, quest_id, category, topics))
    return DialogueCatalog(tuple(result))
