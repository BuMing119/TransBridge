"""Pure action, context and retrieval planners for translation runs."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from transbridge.application.io.stage_policy import StageOperation, StagePolicy

from .models import (
    ActionAssignment,
    ActionPlan,
    ActionRuleSpec,
    ContextBatch,
    ContextPlan,
    PlanningEntry,
    RetrievalSnapshot,
    RetrievalStatus,
    TranslationAction,
    TranslationRunSpec,
)

_ROUND1 = {
    "种族与派系": frozenset({"RACE:FULL", "RACE:DESC", "CLAS:FULL"}),
    "人名": frozenset({"NPC_:FULL", "NPC_:SHRT", "TACT:FULL"}),
    "地名": frozenset({"LCTN:FULL", "WRLD:FULL", "CELL:FULL", "DOOR:FULL", "REFR:FULL"}),
    "书名": frozenset({"BOOK:FULL", "SCRL:FULL"}),
    "物品": frozenset({
        "ACTI:FULL",
        "ACTI:RNAM",
        "ALCH:FULL",
        "AMMO:FULL",
        "AMMO:DESC",
        "ARMO:DESC",
        "ARMO:FULL",
        "CONT:FULL",
        "FLOR:FULL",
        "INGR:FULL",
        "KEYM:FULL",
        "MISC:FULL",
        "PROJ:FULL",
        "SLGM:FULL",
        "TREE:FULL",
        "WEAP:DESC",
        "WEAP:FULL",
    }),
    "法术技能": frozenset({
        "AVIF:DESC",
        "AVIF:FULL",
        "ENCH:FULL",
        "EXPL:FULL",
        "MESG:DESC",
        "MESG:FULL",
        "MESG:ITXT",
        "MGEF:DNAM",
        "MGEF:FULL",
        "PERK:DESC",
        "PERK:EPF2",
        "PERK:EPFD",
        "PERK:FULL",
        "SHOU:DESC",
        "SHOU:FULL",
        "SPEL:DESC",
        "SPEL:FULL",
    }),
    "任务名": frozenset({"QUST:FULL"}),
    "互动": frozenset({"FLOR:RNAM", "FURN:FULL", "HAZD:FULL"}),
}
_ROUND2_PREFIXES = frozenset({"INFO", "DIAL"})
_ROUND3 = frozenset({"BOOK:DESC", "QUST:NNAM", "QUST:CNAM", "LSCR:DESC", "SCRL:DESC"})


class ActionPlanner:
    """Build a complete, mutually exclusive partition after StagePolicy exclusion."""

    def __init__(self, stage_policy: StagePolicy | None = None) -> None:
        self._stage_policy = stage_policy or StagePolicy()

    def plan(
        self,
        entries: Iterable[PlanningEntry],
        rules: Iterable[ActionRuleSpec],
    ) -> ActionPlan:
        entry_list = tuple(entries)
        ordered_rules = tuple(sorted(rules, key=lambda rule: (rule.priority, rule.rule_id)))
        assignments: list[ActionAssignment] = []
        for entry in entry_list:
            decision = self._stage_policy.evaluate(
                entry.stage,
                entry.translation,
                StageOperation.AI,
                original=entry.original,
            )
            if not decision.include_ai:
                assignments.append(ActionAssignment(entry.key, TranslationAction.SKIP, "stage_policy"))
                continue
            matched = next((rule for rule in ordered_rules if _matches(rule, entry)), None)
            if matched is None:
                assignments.append(ActionAssignment(entry.key, TranslationAction.SKIP, "no_matching_rule"))
            else:
                assignments.append(ActionAssignment(entry.key, matched.action, "rule", matched.rule_id))
        scope = tuple(entry.key for entry in entry_list)
        if len(set(scope)) != len(scope):
            raise ValueError("planning entries must have unique keys")
        return ActionPlan(scope, tuple(assignments))


class ContextPlanner:
    """Assign each actionable entry to one ordered context batch exactly once."""

    def __init__(self, max_chars: int = 6000) -> None:
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars <= 0:
            raise ValueError("max_chars must be a positive integer")
        self._max_chars = max_chars

    def plan(
        self,
        entries: Iterable[PlanningEntry],
        action_plan: ActionPlan,
    ) -> ContextPlan:
        by_key = {entry.key: entry for entry in entries}
        actionable = {
            assignment.key for assignment in action_plan.assignments if assignment.action is not TranslationAction.SKIP
        }
        if not actionable <= by_key.keys():
            raise ValueError("ActionPlan contains entries unavailable to ContextPlanner")

        groups: dict[tuple[int, str, str], list[PlanningEntry]] = {}
        diagnostics: list[str] = []
        group_order: list[tuple[int, str, str]] = []
        for key in action_plan.scope:
            if key not in actionable:
                continue
            entry = by_key[key]
            round_number, category, quest_id, diagnostic = _classify(entry.context)
            group_key = (round_number, category, quest_id)
            if group_key not in groups:
                groups[group_key] = []
                group_order.append(group_key)
            groups[group_key].append(entry)
            if diagnostic:
                diagnostics.append(f"{entry.key.serialize()}:{diagnostic}")

        batches: list[ContextBatch] = []
        quest_sequences: dict[str, int] = {}
        for round_number, category, quest_id in sorted(
            group_order, key=lambda value: (value[0], group_order.index(value))
        ):
            chunks = _split(groups[(round_number, category, quest_id)], self._max_chars)
            for chunk in chunks:
                sequence = None
                if round_number == 2:
                    sequence = quest_sequences.get(quest_id, 0)
                    quest_sequences[quest_id] = sequence + 1
                batches.append(
                    ContextBatch(
                        round_number,
                        category,
                        tuple(entry.key for entry in chunk),
                        quest_id,
                        sequence,
                    )
                )
        plan = ContextPlan(tuple(batches), tuple(diagnostics))
        if len(plan.keys) != len(set(plan.keys)) or set(plan.keys) != actionable:
            raise RuntimeError("ContextPlanner did not produce a complete unique assignment")
        return plan


def build_run_spec(
    *,
    run_id: str,
    config_revision: int,
    input_revision: int,
    source_locale: str,
    target_locale: str,
    prompt_profile: str,
    provider: str,
    base_url: str,
    model: str,
    parameters: Mapping[str, Any],
    retrieval_enabled: bool,
    retrieval_loader: Callable[[], Iterable[str]] | None,
    scope: Iterable[Any],
) -> TranslationRunSpec:
    retrieval = _retrieval_snapshot(retrieval_enabled, retrieval_loader)
    frozen_parameters = tuple(sorted((key, _stable_value(value)) for key, value in parameters.items()))
    return TranslationRunSpec(
        run_id,
        config_revision,
        input_revision,
        source_locale,
        target_locale,
        prompt_profile,
        provider,
        base_url,
        model,
        frozen_parameters,
        retrieval,
        tuple(scope),
    )


def _retrieval_snapshot(
    enabled: bool,
    loader: Callable[[], Iterable[str]] | None,
) -> RetrievalSnapshot:
    if not enabled:
        return RetrievalSnapshot(RetrievalStatus.DISABLED)
    if loader is None:
        return RetrievalSnapshot(RetrievalStatus.DEGRADED, reason_code="RETRIEVAL_CAPABILITY_UNAVAILABLE")
    try:
        manifest = tuple(loader())
    except Exception:
        return RetrievalSnapshot(RetrievalStatus.DEGRADED, reason_code="RETRIEVAL_MANIFEST_LOAD_FAILED")
    return RetrievalSnapshot(RetrievalStatus.AVAILABLE, manifest)


def _matches(rule: ActionRuleSpec, entry: PlanningEntry) -> bool:
    if rule.stages is not None and entry.stage not in rule.stages:
        return False
    if rule.labels is not None and not rule.labels.intersection(entry.labels):
        return False
    context = entry.context.split("|", 1)[0]
    return rule.contexts is None or context in rule.contexts


def _classify(context: str) -> tuple[int, str, str, str | None]:
    base, separator, quest = context.partition("|")
    for category, contexts in _ROUND1.items():
        if base in contexts:
            return 1, category, "", None
    record_type = base.partition(":")[0]
    if record_type in _ROUND2_PREFIXES:
        return 2, "对话", quest if separator else "", None
    if base in _ROUND3:
        return 3, "长文本", "", None
    return 3, "未分类", "", "CONTEXT_FALLBACK_ROUND3"


def _split(entries: list[PlanningEntry], max_chars: int) -> list[list[PlanningEntry]]:
    chunks: list[list[PlanningEntry]] = []
    current: list[PlanningEntry] = []
    size = 0
    for entry in entries:
        entry_size = len(entry.original) + len(entry.key.local_key)
        if current and size + entry_size > max_chars:
            chunks.append(current)
            current = []
            size = 0
        current.append(entry)
        size += entry_size
    if current:
        chunks.append(current)
    return chunks


def _stable_value(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
