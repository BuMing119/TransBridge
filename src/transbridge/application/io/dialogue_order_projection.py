# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Project complete-source dialogue blocks without changing local matching data."""

from collections import Counter, defaultdict
import logging
import re

from transbridge.application.dialogue_order import Branch, FieldOrder, Item, Link, Topic, plugin_only_layout
from transbridge.converter.plugin_entry_metadata import PLUGIN_PARENT_DIAL_FORMID, restore_plugin_source_order
from transbridge.parser.plugin.dialogue_order_binary import check_cancelled

from .dialogue_question_order import order_topic_questions
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
    """Project verified scene, topic and INFO groups into their quest's original slots.

    Evidence belongs to one complete source. Scene references and questions
    can still be ordered when the topic link graph is invalid but membership is known.
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
    scene_topics = defaultdict(dict)
    for scene in evidence.scenes:
        if not scene.quest_id:
            continue
        for topic_id in scene.topic_ids:
            topic = records.get(topic_id)
            if (
                topic
                and topic.valid
                and not topic.deleted
                and topic.kind == "DIAL"
                and topic.quest_id == scene.quest_id
            ):
                scene_topics[scene.quest_id].setdefault(topic_id, None)
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
        known = []
        for member in members:
            item = _item(member[1], records, source_id, quest)
            if item is not None:
                known.append((member, item))
        items = [item for _, item in known]
        live_edges = [edge for edge in edges[quest] if not records[edge.source_info_id].deleted]
        if (
            not quest
            or not topics[quest]
            or quest in evidence.invalid_quests
            or any(not edge.valid for edge in live_edges)
        ):
            _fallback(quest, "missing or invalid quest relations", reports)
            graph_order = None
        elif len(known) != len(members) or any(not record.valid for record in topics[quest]):
            _fallback(quest, "entries cannot be uniquely located in the captured DIAL/INFO records", reports)
            graph_order = None
        else:
            result = _topic_layout(
                source_id, quest, topics[quest], branches[quest], live_edges, items, records, evidence
            )
            if reports is not None:
                reports[quest] = result
            if result["diagnostics"]:
                _logger.warning("DIALOGUE_ORDER_LAYOUT %s: %s", quest, result["diagnostics"])
            graph_order = result["ordered_topic_ids"] if result["status"] == "laid_out" else None
        # The graph ranks unclaimed topics. Explicit scene/action membership takes precedence.
        preferred = scene_topics.get(quest, {})
        blocks = defaultdict(list)
        for (_, entry), item in known:
            blocks[item.topic_id].append(entry)
        topic_order = tuple(dict.fromkeys((*preferred, *(graph_order if graph_order is not None else blocks))))
        if reports is not None:
            reports[quest]["reading_topic_ids"] = topic_order
        question_blocks = {topic: order_topic_questions(rows) for topic, rows in blocks.items()}
        if graph_order is None:
            # Unreliable links cannot justify regrouping orphan topics. Keep their source slots.
            queues = {topic: iter(rows) for topic, rows in question_blocks.items()}
            rows = [row for topic in preferred for row in question_blocks.get(topic, ())]
            rows.extend(next(queues[item.topic_id]) for item in items if item.topic_id not in preferred)
        else:
            rows = [row for topic in topic_order for row in question_blocks.get(topic, ())]
        for ((index, previous), _), entry in zip(known, rows, strict=True):
            ordered[index] = entry
            projected |= entry is not previous
        projected |= graph_order is not None
    check_cancelled(cancellation)
    return tuple(ordered), projected


def _topic_layout(source_id, quest, topics, branches, live_edges, items, records, evidence):
    invalid_preferences = quest in evidence.invalid_branch_quests or any(
        topic.branch_id and (topic.branch_id not in records or not records[topic.branch_id].valid) for topic in topics
    )
    return plugin_only_layout(
        [Topic(topic.form_id, source_id, quest, topic.record_ordinal) for topic in topics],
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
                tuple(topic.form_id for topic in topics if topic.branch_id == branch.form_id),
            )
            for branch in branches
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


def _item(entry, records, source_id, quest):
    match = _KEY_ID.fullmatch(entry.identity.local_key)
    record = records.get(match[1].upper()) if match else None
    context, _ = parse_ordered_context(entry.context)
    kind = (context or "").partition(":")[0]
    if record is None or record.kind != kind or not record.valid or record.quest_id != quest:
        return None
    topic = record.form_id if kind == "DIAL" else record.parent_topic_id
    # A legacy entry can omit the hint, but cannot contradict the physical parent.
    hinted = dict(entry.metadata).get(PLUGIN_PARENT_DIAL_FORMID)
    if hinted and (not isinstance(hinted, str) or hinted.partition("|")[0].upper() != topic):
        return None
    return Item(entry.identity, topic, source_id, quest, record.form_id if kind == "INFO" else "")


def _fallback(quest, reason, reports):
    _logger.warning("DIALOGUE_ORDER_FALLBACK %s: %s; skipping topic link layout", quest or "unassociated", reason)
    if reports is not None:
        reports[quest] = {"status": "fallback", "reason": reason}
