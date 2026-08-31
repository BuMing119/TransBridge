"""Build XT-style record navigation from explicit plugin relationships, without Qt."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from transbridge.application.io.identity import EntryKey
from transbridge.converter.plugin_entry_metadata import PLUGIN_PARENT_DIAL_FORMID, restore_plugin_source_order
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.parser.plugin.dialogue_catalog import DialogueCatalog, DialogueRecord

QuestId = tuple[str, str]
TopicId = tuple[str, str, str]


@dataclass(frozen=True)
class DialogueTopic:
    identity: TopicId
    label: str
    entries: tuple[EntryKey, ...]
    kind: str = "DIAL"
    tooltip: str = ""


@dataclass(frozen=True)
class DialogueQuest:
    identity: QuestId
    label: str
    topics: tuple[DialogueTopic, ...]


@dataclass(frozen=True)
class DialogueIndex:
    quests: tuple[DialogueQuest, ...]
    locations: dict[EntryKey, tuple[int, int, int]]


def source_unavailable_reason(*, format_id: str | None, esp_path: str | None, eet_path: str | None) -> str | None:
    """A translation overlay never replaces an explicitly identified plugin source."""
    if format_id in {"xml.eet", "binary.eet"} or (not format_id and not esp_path and eet_path):
        return "EET 解析不包含任务／话题关系，任务树不可用；右侧仍可编辑译文。"
    if format_id == "plugin.sse" or (not format_id and esp_path):
        return None
    return "任务树需要 ESP/ESM/ESL 插件解析的任务上下文；右侧仍可编辑译文。"


def record_type(entry: TranslationEntry) -> str:
    return (entry.context or "").split("|", 1)[0].replace(" ", ":").split(":", 1)[0]


def _form_id(entry: TranslationEntry) -> str:
    if entry.form_id_with_plugin:
        return entry.form_id_with_plugin.split("|", 1)[0].upper()
    return entry.key.split("~", 1)[0].rsplit(":", 1)[-1].split("|", 1)[0].upper()


def _editor_id(entry: TranslationEntry) -> str:
    # Hydrated entries retain the EditorID in their key, not the legacy extra field.
    value = entry.editor_id or entry.key.split("~", 1)[0].rpartition(":")[0]
    return value if value and value.lower() not in {"none", "null"} else ""


def _label(kind: str, form_id: str, editor_id: str = "") -> str:
    name = f" {{{editor_id}}}" if editor_id else ""
    return f"{kind}{name} [{form_id}]" if form_id else f"{kind} {{未关联任务}}"


def _topic_id(entry: TranslationEntry) -> str:
    if record_type(entry) == "QUST":
        return "journal"
    if record_type(entry) == "DIAL":
        return _form_id(entry)
    parent = dict(entry.metadata).get(PLUGIN_PARENT_DIAL_FORMID)
    return parent.split("|", 1)[0].upper() if isinstance(parent, str) and parent else "unknown"


def _record_order(identity: str) -> tuple[int, int, str]:
    if identity == "journal":
        return (0, 0, "")
    form_id = identity.removeprefix("SCEN:")
    try:
        return (1, int(form_id, 16), identity)
    except ValueError:
        return (2, 0, identity)


def build_dialogue_index(
    entries: Iterable[TranslationEntry], *, catalog: DialogueCatalog = DialogueCatalog()
) -> DialogueIndex:
    entries = tuple(entries)
    namespaces = {entry.identity.namespace.value for entry in entries}
    # Raw FormIDs belong to one source. A mixed collection cannot safely attach
    # the same catalog to every namespace, so keep only its per-entry metadata.
    records = catalog.records if len(namespaces) == 1 else ()
    topics_by_id = {record.form_id: record for record in records if record.kind == "DIAL"}
    quest_labels = {
        record.form_id: _label("QUST", record.form_id, record.editor_id) for record in records if record.kind == "QUST"
    }
    scene_quests: dict[str, set[str]] = defaultdict(set)
    for record in records:
        if record.kind == "SCEN" and record.quest_id:
            for topic_id in record.topic_ids:
                scene_quests[topic_id].add(record.quest_id)
    by_quest: dict[QuestId, list[TranslationEntry]] = defaultdict(list)
    entry_quests: dict[str, set[str]] = defaultdict(set)
    labels: dict[TopicId, str] = {}
    for entry in entries:
        kind = record_type(entry)
        if kind not in {"QUST", "DIAL", "INFO"}:
            continue
        namespace, topic_id = entry.identity.namespace.value, _topic_id(entry)
        if kind == "QUST":
            quest_id = _form_id(entry)
            labels[(namespace, quest_id, "journal")] = _label("QUST", quest_id, _editor_id(entry))
        else:
            quest_id = (entry.context or "").partition("|")[2].split("|", 1)[0].upper()
            record = topics_by_id.get(topic_id)
            quest_id = (record.quest_id if record else "") or quest_id
            if not quest_id and len(scene_quests[topic_id]) == 1:
                quest_id = next(iter(scene_quests[topic_id]))
            if kind == "DIAL":
                labels[(namespace, quest_id, topic_id)] = _label("DIAL", topic_id, _editor_id(entry))
            if quest_id:
                entry_quests[topic_id].add(quest_id)
        by_quest[(namespace, quest_id)].append(entry)

    record_nodes: dict[QuestId, dict[str, DialogueRecord]] = defaultdict(dict)
    if records:
        namespace = next(iter(namespaces))
        for record in records:
            if record.kind not in {"DIAL", "SCEN"}:
                continue
            quest_id = record.quest_id
            if record.kind == "DIAL" and not quest_id and len(scene_quests[record.form_id]) == 1:
                quest_id = next(iter(scene_quests[record.form_id]))
            if record.kind == "DIAL" and not quest_id and len(entry_quests[record.form_id]) == 1:
                quest_id = next(iter(entry_quests[record.form_id]))
            quest_key = (namespace, quest_id)
            node_id = f"SCEN:{record.form_id}" if record.kind == "SCEN" else record.form_id
            record_nodes[quest_key][node_id] = record
            by_quest.setdefault(quest_key, [])

    grouped: dict[QuestId, dict[str, list[EntryKey]]] = {}
    topic_entries: dict[tuple[str, str], list[EntryKey]] = defaultdict(list)
    for quest_key, items in by_quest.items():
        nodes: dict[str, list[EntryKey]] = defaultdict(list)
        for entry in restore_plugin_source_order(items):
            topic_id = _topic_id(entry)
            nodes[topic_id].append(entry.identity)
            if topic_id not in {"journal", "unknown"}:
                topic_entries[(quest_key[0], topic_id)].append(entry.identity)
        grouped[quest_key] = nodes

    quests = []
    locations: dict[EntryKey, tuple[int, int, int]] = {}
    for quest_key, nodes in grouped.items():
        namespace, quest_id = quest_key
        definitions = record_nodes[quest_key]
        for node_id, record in definitions.items():
            if record.kind == "SCEN":
                # Explicit action references determine scene contents. Do not create
                # fake SCEN strings or overwrite the canonical DIAL entry locations.
                nodes[node_id] = list(
                    dict.fromkeys(key for topic_id in record.topic_ids for key in topic_entries[(namespace, topic_id)])
                )
            else:
                nodes.setdefault(node_id, [])
        topics = []
        for node_id in sorted(nodes, key=_record_order):
            record = definitions.get(node_id)
            kind = record.kind if record else ("QUST" if node_id == "journal" else "DIAL")
            label = labels.get((*quest_key, node_id), _label("DIAL", node_id))
            if record is not None:
                label = _label(kind, record.form_id, record.editor_id or record.category)
            elif node_id == "unknown":
                label = "DIAL {未关联话题}"
            keys = tuple(nodes[node_id])
            tooltip = f"{label}\n关联词条：{len(keys)}"
            if kind == "SCEN":
                tooltip += "\n场景引用的话题：" + (", ".join(record.topic_ids) or "无")
            elif node_id == "unknown":
                tooltip += "\n缺少父 DIAL 信息，未推测话题关联。"
            topic = DialogueTopic((*quest_key, node_id), label, keys, kind, tooltip)
            if kind != "SCEN":
                for row, key in enumerate(keys):
                    locations[key] = (len(quests), len(topics), row)
            topics.append(topic)
        label = quest_labels.get(quest_id) or labels.get((*quest_key, "journal")) or _label("QUST", quest_id)
        quests.append(DialogueQuest(quest_key, label, tuple(topics)))
    return DialogueIndex(tuple(quests), locations)
