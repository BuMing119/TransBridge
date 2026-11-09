# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Immutable display positions shared by internal entry and dialogue views."""

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from transbridge.parser.plugin.dialogue_order_binary import check_cancelled
from transbridge.parser.plugin.dialogue_order_evidence import DialogueOrderEvidence

from .dialogue_order_projection import _KEY_ID, project_dialogue_order, restore_dialogue_source_order
from .dialogue_reading_cache import find_reading_view, remember_reading_view
from .identity import EntryKey, SourceNamespace


@dataclass(frozen=True, slots=True)
class ReadingEntry:
    """Only immutable source structure crosses the display worker boundary."""

    identity: EntryKey
    context: str | None
    metadata: tuple[tuple[str, Any], ...]


@dataclass(frozen=True, slots=True)
class DialogueReadingView:
    ordered_keys: tuple[EntryKey, ...]
    topic_orders: Mapping[tuple[SourceNamespace, str], tuple[str, ...]]


def build_dialogue_reading_view(
    entries: Iterable[ReadingEntry],
    evidence: DialogueOrderEvidence | None,
    *,
    cancellation: Callable[[], bool] | None = None,
) -> DialogueReadingView:
    """Build complete-source positions without changing any entry or collection.

    Call in a worker and project the resulting keys onto current filtered rows.
    This deliberately never creates export contexts or display metadata.
    """
    check_cancelled(cancellation)
    entries = tuple(entries)
    cached = find_reading_view(entries, evidence)
    if cached is not None:
        check_cancelled(cancellation)
        return cached
    view = _build_reading_view(entries, evidence, cancellation=cancellation)
    check_cancelled(cancellation)
    if evidence is not None and evidence.complete and all(type(entry) is ReadingEntry for entry in entries):
        remember_reading_view(entries, evidence, view)
    check_cancelled(cancellation)
    return view


def _build_reading_view(entries, evidence, *, cancellation=None):
    namespaces = {entry.identity.namespace for entry in entries}
    keys = tuple(entry.identity for entry in entries)
    if evidence is None or not evidence.complete or len(namespaces) != 1 or len(set(keys)) != len(keys):
        return DialogueReadingView(keys, MappingProxyType({}))
    namespace = next(iter(namespaces))
    baseline = restore_dialogue_source_order(entries)
    reports = {}
    ordered, _ = project_dialogue_order(
        baseline, evidence, reports=reports, include_empty_topics=True, cancellation=cancellation
    )
    # Invalid quests still have a conservative tree baseline: visible blocks in
    # source order, followed by zero-output DIALs in physical record order.
    records = {record.form_id: record for record in evidence.records}
    topics = defaultdict(dict)
    for entry in baseline:
        match = _KEY_ID.fullmatch(entry.identity.local_key)
        record = records.get(match[1].upper()) if match else None
        if record is not None and not record.deleted and record.kind in {"DIAL", "INFO"}:
            topic = record.form_id if record.kind == "DIAL" else record.parent_topic_id
            topics[record.quest_id].setdefault(topic, None)
    for record in sorted(evidence.records, key=lambda record: record.record_ordinal):
        if record.kind == "DIAL" and not record.deleted:
            topics[record.quest_id].setdefault(record.form_id, None)
    topic_orders = {}
    for quest, baseline_topics in topics.items():
        check_cancelled(cancellation)
        result = reports.get(quest, {})
        order = result["ordered_topic_ids"] if result.get("status") == "laid_out" else baseline_topics
        topic_orders[namespace, quest] = tuple(order)
    check_cancelled(cancellation)
    return DialogueReadingView(tuple(entry.identity for entry in ordered), MappingProxyType(topic_orders))
