# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Read navigable quest, topic and scene records without creating translation entries."""

from __future__ import annotations

from dataclasses import dataclass
import re

from sse_plugin_interface.group import Group
from sse_plugin_interface.record import Record

from .dialogue_order_binary import check_cancelled
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
CATALOG_VERSION = 1
_FORM_ID = re.compile(r"[0-9A-F]{8}")


def _records(groups, cancelled=None):
    for item in groups:
        check_cancelled(cancelled)
        if isinstance(item, Group):
            yield from _records(item.children, cancelled)
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


def read_dialogue_catalog(plugin: SSEPluginWithContext, *, cancellation=None) -> DialogueCatalog:
    """Use explicit binary references only; record order never implies a link."""
    cancelled = (lambda: cancellation.is_cancelled) if cancellation is not None else None
    records = tuple(_records(plugin.groups, cancelled))
    branches = {record.formid: _field_reference(record, "QNAM") for record in records if record.type == "DLBR"}
    starts = {
        _field_reference(record, "SNAM"): _field_reference(record, "QNAM")
        for record in records
        if record.type == "DLBR" and _field_reference(record, "SNAM")
    }
    result = []
    for record in records:
        check_cancelled(cancelled)
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


def catalog_payload(catalog: DialogueCatalog) -> tuple:
    """Store navigational facts only, with no parser objects or plugin payload."""
    return tuple(
        (item.kind, item.form_id, item.editor_id, item.quest_id, item.category, item.topic_ids)
        for item in catalog.records
    )


def catalog_from_payload(payload) -> DialogueCatalog:
    """Validate the closed, versioned catalog schema at the persistence boundary."""
    if not isinstance(payload, (tuple, list)):
        raise ValueError("Dialogue catalog records must be a sequence")
    records = []
    for row in payload:
        if not isinstance(row, (tuple, list)) or len(row) != 6:
            raise ValueError("Invalid dialogue catalog record")
        kind, form_id, editor_id, quest_id, category, topic_ids = row
        if any(type(value) is not str for value in (kind, form_id, editor_id, quest_id, category)):
            raise ValueError("Dialogue catalog record fields must be text")
        if kind not in {"QUST", "DIAL", "SCEN"} or not _FORM_ID.fullmatch(form_id):
            raise ValueError("Invalid dialogue catalog record identity")
        if quest_id and not _FORM_ID.fullmatch(quest_id):
            raise ValueError("Invalid dialogue catalog quest reference")
        if category and (kind != "DIAL" or category not in _CATEGORIES):
            raise ValueError("Invalid dialogue catalog category")
        if not isinstance(topic_ids, (tuple, list)) or any(
            type(topic) is not str or not _FORM_ID.fullmatch(topic) for topic in topic_ids
        ):
            raise ValueError("Invalid dialogue catalog topic references")
        if len(set(topic_ids)) != len(topic_ids) or (kind != "SCEN" and topic_ids):
            raise ValueError("Invalid dialogue catalog topic membership")
        if kind == "QUST" and quest_id:
            raise ValueError("Quest catalog records cannot reference another quest")
        records.append(DialogueRecord(kind, form_id, editor_id, quest_id, category, tuple(topic_ids)))
    return DialogueCatalog(tuple(records))
