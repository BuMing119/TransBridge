"""Project complete-source dialogue blocks without changing local matching data."""

from collections import Counter, defaultdict
import logging
import re

from transbridge.application.dialogue_order import Branch, FieldOrder, Item, Link, Topic, plugin_only_layout
from transbridge.converter.plugin_entry_metadata import PLUGIN_PARENT_DIAL_FORMID, restore_plugin_source_order
from transbridge.parser.plugin.dialogue_order_binary import check_cancelled

from .paratranz_context_order import parse_ordered_context

_logger = logging.getLogger(__name__)
_KEY_ID = re.compile(r"[^:]*:([0-9A-Fa-f]{8})\|[0-9]+(?:~.*)?\Z")


def restore_dialogue_source_order(entries):
    """Restore each quest's source baseline without moving unrelated slots."""
    groups = defaultdict(list)
    for index, entry in enumerate(entries):
        base, _ = parse_ordered_context(entry.context)
        kind, _, quest = (base or "").partition("|")
        if kind.partition(":")[0] in {"DIAL", "INFO"}:
            groups[quest.upper()].append((index, entry))
    ordered = list(entries)
    for members in groups.values():
        restored = restore_plugin_source_order(entry for _, entry in members)
        for (index, _), entry in zip(members, restored, strict=True):
            ordered[index] = entry
    return tuple(ordered)


def project_dialogue_order(entries, evidence, *, reports=None, include_empty_topics=False, cancellation=None):
    """Keep each quest's original slots and all objects; move only whole DIALs.

    Evidence belongs to this one complete source. Missing or invalid evidence
    retains the baseline with an explicit diagnostic. Reports are optional local
    validation output and are never used as ranking inputs.
    """
    check_cancelled(cancellation)
    if evidence is None:
        return entries, False
    if evidence.diagnostics:
        _logger.warning("Dialogue relation diagnostics: %s", dict(Counter(item.code for item in evidence.diagnostics)))
    if not evidence.complete:
        _logger.warning("DIALOGUE_ORDER_INCOMPLETE: captured relations unavailable; keeping source order")
        return entries, False
    namespaces = {entry.identity.namespace for entry in entries}
    if len(namespaces) != 1:
        _logger.warning("DIALOGUE_ORDER_SOURCE_SCOPE: expected one source namespace; keeping source order")
        return entries, False
    source_id = next(iter(namespaces)).value
    records = {record.form_id: record for record in evidence.records}
    topics, branches, edges = defaultdict(list), defaultdict(list), defaultdict(list)
    for record in evidence.records:
        if record.deleted:
            continue
        if record.kind == "DIAL":
            topics[record.quest_id].append(record)
        elif record.kind == "DLBR":
            branches[record.quest_id].append(record)
    for edge in evidence.links:
        parent = records.get(edge.source_topic_id)
        if parent is not None:
            edges[parent.quest_id].append(edge)
    slots = defaultdict(list)
    for index, entry in enumerate(entries):
        base, _ = parse_ordered_context(entry.context)
        kind, _, quest = (base or "").partition("|")
        if kind.partition(":")[0] in {"DIAL", "INFO"}:
            slots[quest.upper()].append((index, entry))
    ordered = list(entries)
    projected = False
    if include_empty_topics:
        for quest in topics:
            slots.setdefault(quest, [])
    for quest, members in slots.items():
        check_cancelled(cancellation)
        if not quest or quest in evidence.invalid_quests or not topics[quest]:
            _fallback(quest, "missing or invalid quest relations", reports)
            continue
        items = _items(members, records, source_id, quest)
        if items is None or any(not record.valid for record in topics[quest]):
            _fallback(quest, "entries cannot be uniquely located in the captured DIAL/INFO records", reports)
            continue
        live_edges = [edge for edge in edges[quest] if not records[edge.source_info_id].deleted]
        if any(not edge.valid for edge in live_edges):
            _fallback(quest, "a live INFO has an invalid TCLT target", reports)
            continue
        invalid_preferences = quest in evidence.invalid_branch_quests or any(
            topic.branch_id and (topic.branch_id not in records or not records[topic.branch_id].valid)
            for topic in topics[quest]
        )
        result = plugin_only_layout(
            [Topic(topic.form_id, source_id, quest, topic.record_ordinal) for topic in topics[quest]],
            items,
            [
                Link(edge.source_topic_id, edge.target_topic_id, source_id, quest, edge.source_info_id)
                for edge in live_edges
            ],
            source_id=source_id,
            quest_id=quest,
            branches=[
                Branch(
                    branch.form_id,
                    source_id,
                    quest,
                    branch.start_topic_id,
                    tuple(topic.form_id for topic in topics[quest] if topic.branch_id == branch.form_id),
                )
                for branch in branches[quest]
                if branch.valid
            ],
            field_orders=[
                FieldOrder(
                    edge.source_topic_id,
                    edge.source_info_id,
                    edge.target_topic_id,
                    edge.info_record_ordinal,
                    edge.field_index,
                )
                for edge in live_edges
            ],
            invalid_preferences=invalid_preferences,
        )
        if reports is not None:
            reports[quest] = result
        if result["diagnostics"]:
            _logger.warning("DIALOGUE_ORDER_LAYOUT %s: %s", quest, result["diagnostics"])
        if result["status"] == "fallback":
            continue
        by_identity = {entry.identity: entry for _, entry in members}
        keys = result["ordered_keys"]
        if len(by_identity) != len(members) or Counter(keys) != Counter(by_identity.keys()):
            raise ValueError(f"Dialogue ordering changed entry identity membership for quest {quest}")
        for (index, _), identity in zip(members, keys, strict=True):
            ordered[index] = by_identity[identity]
        projected = True
    check_cancelled(cancellation)
    return tuple(ordered), projected


def _items(members, records, source_id, quest):
    items = []
    for _, entry in members:
        match = _KEY_ID.fullmatch(entry.identity.local_key)
        if match is None:
            return None
        record = records.get(match[1].upper())
        context, _ = parse_ordered_context(entry.context)
        kind = (context or "").partition(":")[0]
        if record is None or record.kind != kind or not record.valid or record.quest_id != quest:
            return None
        topic = record.form_id if kind == "DIAL" else record.parent_topic_id
        # Parent metadata is an additional ownership check, never a substitute
        # for the captured binary INFO group. Old entries can omit this hint.
        hinted = dict(entry.metadata).get(PLUGIN_PARENT_DIAL_FORMID)
        if hinted and (not isinstance(hinted, str) or hinted.partition("|")[0].upper() != topic):
            return None
        items.append(Item(entry.identity, topic, source_id, quest, record.form_id if kind == "INFO" else ""))
    return items


def _fallback(quest, reason, reports):
    _logger.warning("DIALOGUE_ORDER_FALLBACK %s: %s; keeping source order", quest or "unassociated", reason)
    if reports is not None:
        reports[quest] = {"status": "fallback", "reason": reason}
