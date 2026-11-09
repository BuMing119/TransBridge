# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Explicit plugin dialogue relations, independent of strings and output rows."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
import re
from typing import Any

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity

from .dialogue_order_binary import (
    RELEVANT,
    RecordIdentities,
    binary_records,
    check_cancelled,
    parsed_records,
    remember_identity,
)

EXTRACTION_VERSION = 1
_FORM_ID = re.compile(r"[0-9A-F]{8}\Z")
_MISSING_IDENTITY = object()


@dataclass(frozen=True, slots=True)
class DialogueOrderRecord:
    kind: str
    form_id: str
    record_ordinal: int
    parent_topic_id: str = ""
    quest_id: str = ""
    branch_id: str = ""
    start_topic_id: str = ""
    deleted: bool = False
    valid: bool = True


@dataclass(frozen=True, slots=True)
class DialogueOrderLink:
    source_info_id: str
    source_topic_id: str
    target_topic_id: str
    field_index: int
    info_record_ordinal: int
    valid: bool = True


@dataclass(frozen=True, slots=True)
class DialogueOrderEvidence:
    records: tuple[DialogueOrderRecord, ...] = ()
    links: tuple[DialogueOrderLink, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    invalid_quests: tuple[str, ...] = ()
    invalid_topics: tuple[str, ...] = ()
    invalid_branch_quests: tuple[str, ...] = ()
    complete: bool = True


def _cancelled(cancellation):
    return (lambda: cancellation.is_cancelled) if cancellation is not None else None


def read_dialogue_order_evidence(plugin, *, cancellation=None) -> DialogueOrderEvidence:
    """Reuse the already decoded plugin; do not parse strings or reread its path."""
    cancelled = _cancelled(cancellation)
    identities: RecordIdentities = {}
    return _derive(parsed_records(plugin.groups, cancelled, identities=identities), cancelled, identities=identities)


def read_dialogue_order_bytes(content: bytes, *, cancellation=None) -> DialogueOrderEvidence:
    """Recover the same evidence from an older artifact's captured binary bytes."""
    cancelled = _cancelled(cancellation)
    identities: RecordIdentities = {}
    return _derive(binary_records(content, cancelled, identities=identities), cancelled, identities=identities)


def _derive(raw_records, cancelled=None, *, identities: RecordIdentities | None = None) -> DialogueOrderEvidence:
    index_records = identities is None
    if identities is None:
        identities = {}
    relevant = []
    diagnostics = []
    invalid_quests, invalid_topics, invalid_branch_quests = set(), set(), set()

    def diagnose(code, message, record="", quest=""):
        diagnostics.append(
            Diagnostic(
                f"DIALOGUE_ORDER_{code}",
                message,
                DiagnosticSeverity.WARNING,
                details=(("record", record), ("quest", quest)),
            )
        )

    for raw in raw_records:
        if index_records:
            remember_identity(identities, raw.form_id, raw.kind, raw.deleted)
        if raw.kind in RELEVANT:
            relevant.append(raw)
    values: dict[tuple[str, str], str] = {}
    malformed: set[str] = set()
    malformed_branches: set[str] = set()
    malformed_fields: set[tuple[str, str]] = set()
    for raw in relevant:
        check_cancelled(cancelled)
        expected = {"DIAL": ("QNAM", "BNAM"), "DLBR": ("QNAM", "SNAM")}.get(raw.kind, ())
        for name in expected:
            fields = [(index, data) for index, field_name, data in raw.fields if field_name == name]
            refs = {f"{int.from_bytes(data, 'little'):08X}" for _, data in fields if len(data) == 4 and any(data)}
            if any(len(data) != 4 for _, data in fields) or len(refs) > 1:
                malformed_fields.add((raw.form_id, name))
                if raw.kind == "DIAL" and name == "BNAM":
                    malformed_branches.add(raw.form_id)
                else:
                    malformed.add(raw.form_id)
                diagnose(
                    "INVALID_REFERENCE", f"{raw.kind} {raw.form_id} has an ambiguous or malformed {name}.", raw.form_id
                )
            values[raw.form_id, name] = next(iter(refs)) if len(refs) == 1 else ""

    def ref(raw, name):
        return values.get((raw.form_id, name), "")

    def usable(target, expected, source, field):
        if not target:
            return False
        identity = identities.get(target, _MISSING_IDENTITY)
        if identity is None:
            reason = "DUPLICATE_RECORD"
        elif identity is _MISSING_IDENTITY:
            reason = "MISSING_REFERENCE"
        elif identity == "":
            reason = "DELETED_REFERENCE"
        else:
            reason = "WRONG_REFERENCE_TYPE" if identity != expected else ""
        if reason:
            diagnose(
                reason, f"{source} {field} references {target}; expected a live, unique {expected} record.", source
            )
        return not reason

    records: dict[str, DialogueOrderRecord] = {}
    for raw in relevant:
        duplicate = identities[raw.form_id] is None
        if duplicate:
            diagnose("DUPLICATE_RECORD", f"Record {raw.form_id} occurs more than once in this plugin.", raw.form_id)
        records[raw.form_id] = DialogueOrderRecord(
            raw.kind,
            raw.form_id,
            raw.ordinal,
            raw.parent,
            ref(raw, "QNAM") if raw.kind in {"DIAL", "DLBR"} else "",
            ref(raw, "BNAM"),
            ref(raw, "SNAM"),
            raw.deleted,
            not raw.deleted and raw.form_id not in malformed and not duplicate,
        )
        if raw.deleted:
            diagnose(
                "DELETED_RECORD", f"Deleted {raw.kind} {raw.form_id} is excluded from dialogue ordering.", raw.form_id
            )
    branches = [raw for raw in relevant if raw.kind == "DLBR"]
    branch_quests = {}
    for raw in branches:
        branch = records[raw.form_id]
        valid = branch.valid
        quest_ok = usable(branch.quest_id, "QUST", raw.form_id, "QNAM")
        if (
            quest_ok
            and not raw.deleted
            and identities[raw.form_id] is not None
            and (raw.form_id, "QNAM") not in malformed_fields
        ):
            branch_quests[raw.form_id] = branch.quest_id
        valid &= quest_ok
        valid &= usable(branch.start_topic_id, "DIAL", raw.form_id, "SNAM")
        records[raw.form_id] = replace(branch, valid=bool(valid))

    starts: dict[str, set[str]] = defaultdict(set)
    for branch in records.values():
        if branch.kind == "DLBR" and branch.valid:
            starts[branch.start_topic_id].add(branch.quest_id)
    for raw in relevant:
        if raw.kind != "DIAL":
            continue
        topic = records[raw.form_id]
        candidates = {topic.quest_id} if topic.quest_id else set()
        branch_ok = not topic.branch_id or usable(topic.branch_id, "DLBR", topic.form_id, "BNAM")
        if topic.branch_id and branch_ok:
            branch = records[topic.branch_id]
            if topic.branch_id in branch_quests:
                candidates.add(branch_quests[topic.branch_id])
        if not candidates:
            candidates.update(starts.get(topic.form_id, ()))
        quest_id = next(iter(candidates)) if len(candidates) == 1 else ""
        if (
            not branch_ok
            or topic.form_id in malformed_branches
            or (topic.branch_id and not records.get(topic.branch_id, topic).valid)
        ):
            invalid_branch_quests.update(candidates)
        valid = topic.valid
        if len(candidates) > 1:
            diagnose(
                "CONFLICTING_QUEST", f"DIAL {topic.form_id} has conflicting explicit quest relations.", topic.form_id
            )
            invalid_quests.update(candidates)
            valid = False
        elif quest_id and not usable(quest_id, "QUST", topic.form_id, "QNAM/BNAM/SNAM"):
            valid = False
            invalid_quests.add(quest_id)
        elif not quest_id:
            diagnose("MISSING_QUEST", f"DIAL {topic.form_id} has no usable explicit quest relation.", topic.form_id)
            valid = False
        if not valid and not topic.deleted:
            invalid_topics.add(topic.form_id)
            invalid_quests.update(candidates)
        records[topic.form_id] = replace(topic, quest_id=quest_id, valid=valid)

    # A corrupt start hint disables branch preferences without changing a
    # separately validated DLBR.QNAM ownership relation or the task graph.
    for raw in branches:
        branch = records[raw.form_id]
        target = records.get(branch.start_topic_id)
        if branch.valid and (target is None or not target.valid or target.quest_id != branch.quest_id):
            diagnose(
                "INVALID_BRANCH_START",
                f"DLBR {branch.form_id} has a start topic outside its quest.",
                branch.form_id,
                branch.quest_id,
            )
            records[raw.form_id] = replace(branch, valid=False)
    invalid_branch_quests.update(
        branch.quest_id for branch in records.values() if branch.kind == "DLBR" and not branch.valid and branch.quest_id
    )

    links = []
    for raw in relevant:
        if raw.kind != "INFO":
            continue
        check_cancelled(cancelled)
        info = records[raw.form_id]
        parent_ok = usable(raw.parent, "DIAL", raw.form_id, "parent GRUP")
        parent = records.get(raw.parent)
        quest = parent.quest_id if parent is not None else ""
        valid = info.valid and parent_ok and parent is not None and parent.valid
        if not raw.parent:
            diagnose("MISSING_PARENT", f"INFO {raw.form_id} has no topic-children parent GRUP.", raw.form_id)
        if not valid and raw.parent and not raw.deleted:
            invalid_topics.add(raw.parent)
            if quest:
                invalid_quests.add(quest)
        records[raw.form_id] = replace(info, quest_id=quest, valid=valid)
        for index, name, data in raw.fields:
            if name != "TCLT":
                continue
            target_id = f"{int.from_bytes(data, 'little'):08X}" if len(data) == 4 else ""
            target_ok = bool(target_id and any(data)) and usable(target_id, "DIAL", raw.form_id, "TCLT")
            target = records.get(target_id)
            edge_valid = valid and target_ok and target is not None and target.valid
            if len(data) != 4 or not any(data):
                diagnose(
                    "INVALID_TCLT", f"INFO {raw.form_id} has an invalid TCLT at field {index}.", raw.form_id, quest
                )
            if target is not None and quest and target.quest_id and target.quest_id != quest:
                diagnose(
                    "CROSS_QUEST_LINK",
                    f"INFO {raw.form_id} links from quest {quest} to {target.quest_id}.",
                    raw.form_id,
                    quest,
                )
                if not raw.deleted:
                    invalid_quests.add(target.quest_id)
                edge_valid = False
            if not edge_valid and not raw.deleted:
                if quest:
                    invalid_quests.add(quest)
                if raw.parent:
                    invalid_topics.add(raw.parent)
            links.append(DialogueOrderLink(raw.form_id, raw.parent, target_id, index, raw.ordinal, bool(edge_valid)))
    return DialogueOrderEvidence(
        tuple(sorted(records.values(), key=lambda item: item.record_ordinal)),
        tuple(links),
        tuple(diagnostics),
        tuple(sorted(invalid_quests)),
        tuple(sorted(invalid_topics)),
        tuple(sorted(invalid_branch_quests)),
    )


def evidence_payload(evidence: DialogueOrderEvidence) -> tuple:
    """Only existing artifact-codec primitive contracts cross persistence."""
    return (
        tuple(tuple(getattr(record, name) for name in record.__dataclass_fields__) for record in evidence.records),
        tuple(tuple(getattr(link, name) for name in link.__dataclass_fields__) for link in evidence.links),
        tuple((item.code, item.message, item.details) for item in evidence.diagnostics),
        evidence.invalid_quests,
        evidence.invalid_topics,
        evidence.invalid_branch_quests,
        evidence.complete,
    )


def evidence_from_payload(payload: Any) -> DialogueOrderEvidence:
    if not isinstance(payload, tuple) or len(payload) != 7:
        raise ValueError("Invalid dialogue evidence metadata payload")
    records, links, diagnostics, quests, topics, branch_quests, complete = payload
    result = DialogueOrderEvidence(
        tuple(DialogueOrderRecord(*record) for record in records),
        tuple(DialogueOrderLink(*link) for link in links),
        tuple(
            Diagnostic(code, message, DiagnosticSeverity.WARNING, details=details)
            for code, message, details in diagnostics
        ),
        tuple(quests),
        tuple(topics),
        tuple(branch_quests),
        complete,
    )
    # Strict structural checks keep corrupted/stale metadata from silently
    # asserting relations; the caller can derive it again from captured bytes.
    if type(complete) is not bool or any(type(value) is not str for value in (*quests, *topics, *branch_quests)):
        raise ValueError("Invalid dialogue evidence metadata scope")
    for record in result.records:
        if any(
            type(getattr(record, name)) is not str
            for name in ("kind", "form_id", "parent_topic_id", "quest_id", "branch_id", "start_topic_id")
        ):
            raise ValueError("Invalid dialogue evidence record identity")
        if record.kind not in {"QUST", "DIAL", "INFO", "DLBR"} or not _FORM_ID.fullmatch(record.form_id):
            raise ValueError("Invalid dialogue evidence record kind or FormID")
        if any(
            value and not _FORM_ID.fullmatch(value)
            for value in (record.parent_topic_id, record.quest_id, record.branch_id, record.start_topic_id)
        ):
            raise ValueError("Invalid dialogue evidence record reference")
        if (
            type(record.record_ordinal) is not int
            or record.record_ordinal < 0
            or type(record.valid) is not bool
            or type(record.deleted) is not bool
        ):
            raise ValueError("Invalid dialogue evidence record state")
    for link in result.links:
        if any(
            type(getattr(link, name)) is not str for name in ("source_info_id", "source_topic_id", "target_topic_id")
        ):
            raise ValueError("Invalid dialogue evidence link identity")
        if (
            type(link.field_index) is not int
            or link.field_index < 0
            or type(link.info_record_ordinal) is not int
            or link.info_record_ordinal < 0
            or type(link.valid) is not bool
        ):
            raise ValueError("Invalid dialogue evidence link position")
    by_id = {record.form_id: record for record in result.records}
    if len(by_id) != len(result.records):
        raise ValueError("Duplicate dialogue evidence record identity")
    for link in result.links:
        source = by_id.get(link.source_info_id)
        if source is None:
            raise ValueError("Dialogue evidence link has no source record")
        if not link.valid:
            continue  # Missing/deleted/wrong-type targets are explicit evidence.
        parent, target = by_id.get(link.source_topic_id), by_id.get(link.target_topic_id)
        if (
            source.kind != "INFO"
            or source.parent_topic_id != link.source_topic_id
            or source.record_ordinal != link.info_record_ordinal
            or parent is None
            or target is None
            or parent.kind != "DIAL"
            or target.kind != "DIAL"
            or any(record.deleted or not record.valid for record in (source, parent, target))
            or not parent.quest_id
            or source.quest_id != parent.quest_id
            or target.quest_id != parent.quest_id
        ):
            raise ValueError("Dialogue evidence valid link contradicts its source or target records")
    return result
