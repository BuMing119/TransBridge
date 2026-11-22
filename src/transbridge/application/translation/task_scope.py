# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Immutable source inputs shared by translation task entry points."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from transbridge.application.io.identity import EntryKey
from transbridge.application.io.stage_policy import DEFAULT_STAGE_POLICY
from transbridge.application.workbench_filters import FilterState, filtered_rows
from transbridge.converter.context_categories import context_category
from transbridge.converter.translation_entry import STAGE_HIDDEN, STAGE_LOCKED

from .ai_execution_profile import AiExecutionProfile
from .entry_alias import ai_entry_id


@dataclass(frozen=True, slots=True)
class TranslationScope:
    stage_filters: frozenset[int] = frozenset()
    label_filters: frozenset[str] = frozenset()
    category_filters: frozenset[str] = frozenset()
    preset: str | None = None
    selected_entry_ids: frozenset[str] = frozenset()

    @classmethod
    def default_for_mode(cls, mode):
        if mode == "mixed":
            return cls()
        return cls(stage_filters=frozenset({1, 2, 3, 5} if mode == "polish" else {0}))

    @classmethod
    def from_filters(cls, scope):
        if not isinstance(scope, dict):
            raise ValueError("翻译范围必须是筛选条件对象。")
        if scope.get("action", "include") not in ("include", "only"):
            raise ValueError("当前翻译范围只支持包含匹配词条，请重新设置范围。")
        filters = []
        for name, expected in (("stages", int), ("labels", str), ("categories", str)):
            values = scope.get(name, [])
            if not isinstance(values, list) or any(type(value) is not expected for value in values):
                raise ValueError(f"翻译范围 {name} 必须是 {expected.__name__} 列表。")
            filters.append(frozenset(values))
        return cls(*filters, preset="all" if scope.get("preset") == "all" else None)

    def candidates(self, entries, labels, *, category_of=None):
        rows = filtered_rows(entries, FilterState(), labels)
        return self.candidates_from_rows(rows, category_of=category_of)

    def candidates_from_rows(self, rows, *, category_of=None):
        targets = set()
        if self.preset == "selection":
            for entry_id in self.selected_entry_ids:
                if entry_id.startswith("["):
                    try:
                        targets.add(EntryKey.deserialize(entry_id))
                        continue
                    except ValueError:
                        pass  # A legacy display ID can also start with a bracket.
                targets.add(entry_id)
        selected = []
        for entry, _, membership in rows:
            if entry.stage in (STAGE_LOCKED, STAGE_HIDDEN):
                continue
            if (
                self.preset == "selection"
                and getattr(entry, "identity", None) not in targets
                and ai_entry_id(entry) not in targets
            ):
                continue
            if self.stage_filters and entry.stage not in self.stage_filters:
                continue
            if self.label_filters and not self.label_filters.intersection(membership):
                continue
            if self.category_filters:
                category = category_of(entry) if category_of else context_category(entry.context or "")
                if category not in self.category_filters:
                    continue
            selected.append(entry)
        return selected


class TaskEntryGroups(NamedTuple):
    translate: tuple
    polish: tuple


def task_entry_groups(candidates, *, mode, config, overwrite=False, memberships=None):
    """Partition the selected rows exactly as the desktop translation task will execute them."""
    eligible = [
        entry
        for entry in candidates
        if entry.original.strip()
        and DEFAULT_STAGE_POLICY.allows_ai(entry.stage, entry.translation, original=entry.original)
    ]
    translate, polish = (), ()
    if not eligible:
        return TaskEntryGroups((), ())
    if mode == "mixed":
        from .planning import entry_actions

        actions = entry_actions(config.action_rules, eligible, memberships)
        translate = tuple(
            e for e in eligible if actions[e.identity] == "translate" and (not e.translation or e.stage == 0)
        )
        polish = tuple(e for e in eligible if actions[e.identity] == "polish" and e.translation)
    elif mode == "polish":
        polish = tuple(e for e in eligible if e.translation)
    elif mode == "translate":
        translate = tuple(e for e in eligible if overwrite or not e.translation or e.stage == 0)
    else:
        raise ValueError(f"无效翻译模式：{mode}")
    if polish and not AiExecutionProfile.from_config(mode, config).has_proofread_work:
        polish = ()
    return TaskEntryGroups(translate, polish)


@dataclass(frozen=True, slots=True)
class SourceTask:
    key: str
    label: str
    esp_path: str | None
    collection: object
    translate_entries: tuple
    polish_entries: tuple
    postprocess_entries: tuple = ()

    @property
    def entries(self) -> tuple:
        return self.translate_entries + self.polish_entries + self.postprocess_entries
