"""
配置管理器 —— 向后兼容层。

实际实现已迁移至 src/transbridge/config/，
本文件保留 ActionRule + re-export 以兼容旧 import 路径。
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
import uuid

# ── Re-export（向后兼容）──────────────────────────────────
from transbridge.config.llm import EmbeddingConfig, LLMConfig  # noqa: F401
from transbridge.config.paratranz import ParatranzConfig  # noqa: F401

if TYPE_CHECKING:
    from transbridge.converter.translation_entry import TranslationEntry


# ── ActionRule ────────────────────────────────────────────


@dataclass
class ActionRule:
    """混合模式下的一条动作分配规则。

    规则按 priority 升序匹配（数字越小优先级越高），命中第一条规则后停止。
    filter 为 None 表示该维度不做限制。
    """

    rule_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    priority: int = 0
    status_filter: set[int] | None = None
    label_filter: set[str] | None = None
    category_filter: set[str] | None = None
    action: str = "skip"

    def match(self, entry: "TranslationEntry") -> bool:
        if self.status_filter is not None and entry.stage not in self.status_filter:
            return False
        if self.label_filter is not None:
            entry_labels = getattr(entry, "labels", set()) or set()
            if not self.label_filter.intersection(entry_labels):
                return False
        if self.category_filter is not None:
            if not hasattr(entry, "context") or not entry.context:
                return False
            cat = entry.context.split("|")[0] if "|" in entry.context else entry.context
            from transbridge.converter.context_categories import context_category

            if cat not in self.category_filter and context_category(entry.context) not in self.category_filter:
                return False
        return True

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "priority": self.priority,
            "status_filter": list(self.status_filter) if self.status_filter else None,
            "label_filter": list(self.label_filter) if self.label_filter else None,
            "category_filter": list(self.category_filter) if self.category_filter else None,
            "action": self.action,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ActionRule":
        return cls(
            rule_id=d.get("rule_id", uuid.uuid4().hex[:8]),
            priority=d.get("priority", 0),
            status_filter=set(d["status_filter"]) if d.get("status_filter") else None,
            label_filter=set(d["label_filter"]) if d.get("label_filter") else None,
            category_filter=set(d["category_filter"]) if d.get("category_filter") else None,
            action=d.get("action", "skip"),
        )


def apply_rules(rules: list, entries: list) -> dict:
    """按规则优先级为条目分配动作。

    Args:
        rules: ActionRule 列表（已按 priority 排序）
        entries: TranslationEntry 列表

    Returns:
        {entry_id: action} 映射，未匹配的条目默认 "skip"
    """
    from transbridge.application.translation import (
        ActionPlanner,
        ActionRuleSpec,
        PlanningEntry,
        TranslationAction,
    )

    planning_entries = [
        PlanningEntry(
            entry.identity,
            entry.stage,
            entry.original,
            entry.translation,
            entry.context or "",
            frozenset(getattr(entry, "labels", set()) or set()),
        )
        for entry in entries
    ]
    rule_specs = [
        ActionRuleSpec(
            rule.rule_id,
            rule.priority,
            TranslationAction(rule.action),
            None if rule.status_filter is None else frozenset(rule.status_filter),
            None if rule.label_filter is None else frozenset(rule.label_filter),
            None if rule.category_filter is None else frozenset(rule.category_filter),
        )
        for rule in rules
    ]
    assignments = ActionPlanner().plan(planning_entries, rule_specs).assignments
    by_key = {entry.identity: entry for entry in entries}
    return {by_key[assignment.key].id: assignment.action.value for assignment in assignments}
