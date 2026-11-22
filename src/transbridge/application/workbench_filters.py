# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Workbench table filters over in-memory and revision-bound project rows."""

from __future__ import annotations

from dataclasses import dataclass, replace

from transbridge.converter.context_categories import context_category
from transbridge.converter.translation_entry import STAGE_LABELS


def entry_label_key(entry) -> str:
    """Keep legacy label IDs while disambiguating original-matched entries."""
    return entry.identity.serialize() if getattr(entry, "requires_original_match", False) else entry.id


def entry_category(entry) -> str:
    return context_category(entry.context or "")


_SEARCH_KEYS = {"all": "search_all", "key": "search_key", "original": "search_orig", "translation": "search_trans"}


@dataclass(frozen=True, slots=True)
class FilterState:
    categories: frozenset[str] = frozenset()
    stages: frozenset[int] = frozenset()
    labels: frozenset[str] = frozenset()
    search_all: str = ""
    search_key: str = ""
    search_original: str = ""
    search_translation: str = ""
    focus_labeled: bool = False

    @classmethod
    def from_mapping(cls, value):
        value = dict(value or {})
        if "search_query" in value:
            query = value.get("search_query") or ""
            field = value.get("search_field") or "original"
            field = "original" if field == "text" else field
            if field not in _SEARCH_KEYS:
                if query:
                    raise ValueError(f"工作台不支持此搜索字段：{field}")
            else:
                value[_SEARCH_KEYS[field]] = query
        return cls(
            categories=frozenset(value.get("category") or ()),
            stages=frozenset(value.get("stage") or ()),
            labels=frozenset(value.get("label") or value.get("labels") or ()),
            search_all=str(value.get("search_all") or ""),
            search_key=str(value.get("search_key") or ""),
            search_original=str(value.get("search_orig") or ""),
            search_translation=str(value.get("search_trans") or ""),
            focus_labeled=bool(value.get("focus_labeled")),
        )

    def to_mapping(self):
        return {
            "category": sorted(self.categories),
            "stage": sorted(self.stages),
            "label": sorted(self.labels),
            "search_all": self.search_all,
            "search_key": self.search_key,
            "search_orig": self.search_original,
            "search_trans": self.search_translation,
            "focus_labeled": self.focus_labeled,
        }


def updated_filter_state(current, args, label_library=None):
    """Apply a tool's partial update to the same search boxes and tags as the table."""
    state = FilterState() if args.get("clear") else FilterState.from_mapping(current)
    if (stages := args.get("stages")) is not None:
        if any(type(stage) is not int or stage not in STAGE_LABELS for stage in stages):
            raise ValueError("无效的 stage 值。")
        state = replace(state, stages=frozenset(stages))
    if (categories := args.get("categories")) is not None:
        if not all(isinstance(category, str) and category for category in categories):
            raise ValueError("分类必须是工作台分类名。")
        state = replace(state, categories=frozenset(categories))
    if (labels := args.get("labels")) is not None:
        library = label_library or {}
        ids = set()
        for value in labels:
            if not isinstance(value, str) or not value:
                raise ValueError("标签必须是标签名或 ID。")
            matches = [key for key, info in library.items() if info.get("name") == value]
            if value not in library and len(matches) > 1:
                raise ValueError(f"标签名称不唯一，请使用标签 ID：{value}")
            ids.add(value if value in library or not matches else matches[0])
        state = replace(state, labels=frozenset(ids))
    field = args.get("search_field") or "original"
    if field not in _SEARCH_KEYS:
        raise ValueError(f"工作台不支持此搜索字段：{field}")
    result = state.to_mapping()
    if args.get("search_query") is not None:
        result[_SEARCH_KEYS[field]] = args["search_query"]
    return result


def _in_memory_rows(entries, labels):
    for entry in entries:
        key = entry_label_key(entry)
        yield entry, key, labels.get(key, set()) if entry.id else set()


def filtered_rows(collection, filter_state, entry_labels=None, *, checkpoint=None, observe=None):
    """Yield matching (entry, label key, memberships) from the full source."""
    state = filter_state if isinstance(filter_state, FilterState) else FilterState.from_mapping(filter_state)
    query_rows = getattr(collection, "query_rows", None)
    if callable(query_rows):
        rows = query_rows()
    else:
        rows = _in_memory_rows(collection, entry_labels or {})
    all_kw = state.search_all.lower()
    key_kw = state.search_key.lower()
    orig_kw = state.search_original.lower()
    trans_kw = state.search_translation.lower()
    for entry, key, membership in rows:
        if checkpoint is not None:
            checkpoint()
        if observe is not None:
            observe(entry, key)
        if state.categories and entry_category(entry) not in state.categories:
            continue
        if state.stages and entry.stage not in state.stages:
            continue
        if state.labels and not state.labels.intersection(membership):
            continue
        if state.focus_labeled and not membership:
            continue
        if key_kw and key_kw not in (entry.key or "").lower():
            continue
        if orig_kw and orig_kw not in (entry.original or "").lower():
            continue
        if trans_kw and trans_kw not in (entry.translation or "").lower():
            continue
        if (
            all_kw
            and all_kw
            not in "\n".join((
                entry.key or "",
                entry.original or "",
                entry.translation or "",
                entry.context or "",
            )).lower()
        ):
            continue
        yield entry, key, membership


def filter_entries(collection, filter_state, entry_labels=None):
    if filter_state == FilterState():
        return list(collection)
    return [entry for entry, _key, _labels in filtered_rows(collection, filter_state, entry_labels)]
