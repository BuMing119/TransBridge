"""One source-scoped classification and filename policy for ParaTranz artifacts."""

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
import hashlib
import re

from transbridge.converter.context_categories import EXPORT_CATEGORIES
from transbridge.converter.plugin_entry_metadata import restore_plugin_source_order
from transbridge.converter.translation_entry import TranslationEntry

from .paratranz_context_order import (
    PARATRANZ_CONTEXT_ORDER_METADATA,
    context_orders,
    format_ordered_context,
    parse_ordered_context,
)

_FORM_ID = re.compile(r"[0-9A-Fa-f]{8}\Z")
_KEY_FORM_ID = re.compile(r"[^:]*:([0-9A-Fa-f]{8})\|[0-9]+(?:~.*)?\Z")
_CATEGORY_BY_CONTEXT = {
    context: (filename.removesuffix(".json"), "任务/" + filename if filename == "任务日志.json" else filename)
    for filename, contexts in EXPORT_CATEGORIES.items()
    for context in contexts
}


@dataclass(frozen=True, slots=True)
class ClassifiedGroup:
    category_id: str
    path: str
    entries: tuple[TranslationEntry, ...]


def classify_entries(entries: tuple[TranslationEntry, ...]) -> tuple[ClassifiedGroup, ...]:
    """Classify a complete source before selection; never mutate its identities."""
    ordered = _ordered_entries(tuple(entries))
    titles = _quest_titles(ordered)
    quests = {_quest(entry) for entry in ordered if _quest(entry) is not None}
    filenames = _dialogue_filenames(quests, titles)
    groups = defaultdict(list)
    for entry, order in zip(ordered, context_orders(ordered), strict=True):
        quest = _quest(entry)
        if quest is not None:
            category, path = f"dialogue:{quest or '未关联任务'}", filenames[quest]
        else:
            context, _ = parse_ordered_context(entry.context)
            base = (context or "").partition("|")[0]
            category, path = _CATEGORY_BY_CONTEXT.get(base, ("其他", "其他.json"))
        metadata = dict(entry.metadata)
        if PARATRANZ_CONTEXT_ORDER_METADATA in metadata:
            metadata[PARATRANZ_CONTEXT_ORDER_METADATA] = order
        groups[category, path].append(
            replace(entry, context=format_ordered_context(entry.context, order), metadata=tuple(metadata.items()))
        )
    result = tuple(ClassifiedGroup(category, path, tuple(items)) for (category, path), items in sorted(groups.items()))
    if len({group.path.casefold() for group in result}) != len(result):
        raise ValueError("分类文件路径冲突，未合并不同分类。")
    return result


def path_component(value: str, fallback: str) -> str:
    """Sanitize one filesystem component, preserving plugin extensions on truncation."""
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    if not value:
        return fallback
    if len(value) > 100:
        stem, dot, suffix = value.rpartition(".")
        value = (stem[:85] + "_" + _digest(value) + dot + suffix[:8]) if dot else value[:88] + "_" + _digest(value)
    return value


def _quest(entry: TranslationEntry) -> str | None:
    context, _ = parse_ordered_context(entry.context)
    base, _, quest = (context or "").partition("|")
    if base.split(":", 1)[0] not in {"INFO", "DIAL"}:
        return None
    return quest.upper() if _FORM_ID.fullmatch(quest) else quest


def _quest_titles(entries):
    titles = defaultdict(set)
    for entry in entries:
        context, _ = parse_ordered_context(entry.context)
        if context != "QUST:FULL" or not entry.original.strip():
            continue
        form_id = (entry.form_id_with_plugin or "").partition("|")[0]
        if not _FORM_ID.fullmatch(form_id):
            match = _KEY_FORM_ID.fullmatch(entry.identity.local_key)
            form_id = match[1] if match else ""
        if form_id:
            titles[form_id.upper()].add(entry.original)
    return {quest: next(iter(names)) for quest, names in titles.items() if len(names) == 1}


def _dialogue_filenames(quests, titles):
    labels = {}
    for quest in quests:
        label = titles.get(quest, quest or "未关联任务")
        labels[quest] = path_component(label, quest or "未关联任务")[:80].rstrip(" .")
    suffixes = {quest: quest if _FORM_ID.fullmatch(quest) else _digest(quest) for quest in quests}
    suffix_counts = Counter(suffixes.values())
    for quest, suffix in tuple(suffixes.items()):
        if suffix_counts[suffix] > 1:
            suffixes[quest] = "x" + quest.encode("utf-8").hex()
    names = dict(labels)
    while True:
        counts = Counter(name.casefold() for name in names.values())
        collisions = [quest for quest, name in names.items() if counts[name.casefold()] > 1]
        if not collisions:
            break
        # Distinct underscore-free suffixes distinguish all generated names.
        # Only a literal title (bounded to 80 chars) can shadow one; each round
        # grows that name until no literal title can shadow it again.
        for quest in collisions:
            names[quest] += "_" + suffixes[quest]
    return {quest: f"任务/对话_[{name}].json" for quest, name in names.items()}


def _ordered_entries(entries):
    """Keep source slots; restore dialogue order only with complete unique metadata."""
    groups = defaultdict(list)
    for index, entry in enumerate(entries):
        quest = _quest(entry)
        if quest is not None:
            groups[quest].append((index, entry))
    ordered = list(entries)
    for members in groups.values():
        restored = restore_plugin_source_order(entry for _, entry in members)
        for (index, _), entry in zip(members, restored, strict=True):
            ordered[index] = entry
    return tuple(ordered)


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
