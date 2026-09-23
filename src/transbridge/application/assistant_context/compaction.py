"""Build validated candidates without publishing a head or changing business state."""

from dataclasses import replace
import json
from uuid import uuid4

from transbridge.smart_assistant.context_budget import ContextBudget

from .budget_policy import EMPTY_SUMMARY, BudgetPolicy, current_state_item
from .models import CompactionSummary, ContextEpoch, PreparationWait, encode, state_material, validate_successor
from .ports import SummaryGenerator


def validate_summary(text, items):
    """Validate model semantics' shape and reference scope, never grant authority."""
    try:
        value = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise PreparationWait("COMPACTION_INVALID_OUTPUT", "摘要必须是完整 JSON。") from exc
    valid = isinstance(value, dict) and set(value) == set(EMPTY_SUMMARY)
    if valid:
        valid = isinstance(value["discussion_context"], str)
        for field in ("unresolved_questions", "suggested_next_steps"):
            valid = valid and isinstance(value[field], list) and all(isinstance(s, str) for s in value[field])
        decisions = value["decisions_with_sources"]
        valid = valid and isinstance(decisions, list)
    if not valid:
        raise PreparationWait("COMPACTION_INVALID_OUTPUT", "摘要字段或类型不符合四字段语义合同。")
    allowed = {i.item_id for i in items}
    if not (
        value["discussion_context"].strip()
        or decisions
        or value["unresolved_questions"]
        or value["suggested_next_steps"]
    ):
        raise PreparationWait("COMPACTION_INVALID_OUTPUT", "摘要未保留任何历史语义。")
    for decision in decisions:
        if (
            not isinstance(decision, dict)
            or set(decision) != {"statement", "source_ids"}
            or not isinstance(decision["statement"], str)
            or not isinstance(decision["source_ids"], list)
            or not decision["source_ids"]
            or any(not isinstance(s, str) or s not in allowed for s in decision["source_ids"])
        ):
            raise PreparationWait("COMPACTION_INVALID_OUTPUT", "摘要决定必须引用本次选中原文的准确标识。")
    return text


def _require_state(epoch, required_state):
    latest = current_state_item(epoch)
    actual = state_material(latest)[1] if latest else None
    if encode(actual) != encode(required_state):
        raise PreparationWait("CONTEXT_STATE_CHANGED", "当前权威状态快照与压缩基线不一致。")


def compact(
    epoch: ContextEpoch,
    budget: ContextBudget,
    tools,
    required_state: dict,
    summarizer: SummaryGenerator,
    *,
    enabled=True,
) -> ContextEpoch:
    """Append immutable segments; a failed multi-block attempt exposes candidate_epoch for explicit resume.

    Callers must persist this progress as an unpublished artifact and revalidate identity and sources
    before resuming. The original epoch remains untouched and is the only active context on failure.
    """
    epoch.validate()
    _require_state(epoch, required_state)
    policy = BudgetPolicy()
    current = epoch
    calls = 0
    repaired = False
    feedback = ""
    limit = 2 if budget.measure(epoch.messages, tools).fits else 3
    try:
        while True:
            decision = policy.decide(current, budget, tools)
            if decision.action == "append":
                budget.require(current.messages, tools)
                return current
            if not enabled:
                if budget.measure(current.messages, tools).fits:
                    return current
                raise PreparationWait(
                    "CONTEXT_REQUIRED_TOO_LARGE",
                    f"自动摘要已关闭，当前材料超出本地容量。{budget.measure(current.messages, tools).describe()}。",
                )
            if decision.action == "capacity_wait":
                reason = (
                    "固定规则、工具定义或当前必需材料超过本地容量，压缩旧历史无法解决。"
                    if decision.code == "CONTEXT_REQUIRED_TOO_LARGE"
                    else "现有摘要链与必需材料未给新摘要留下足够空间。"
                )
                raise PreparationWait(decision.code, reason + budget.measure(current.messages, tools).describe() + "。")
            if limit == 2 and current is not epoch:
                raise PreparationWait("CONTEXT_SUMMARY_CAPACITY", "单次压缩仍未低于高水位，请扩大窗口或显式继续。")
            if calls >= limit:
                raise PreparationWait("COMPACTION_CALL_LIMIT", "本次摘要调用额度已用完，请显式继续或扩大窗口。")
            calls += 1
            selected = decision.selected
            wrapper = CompactionSummary("0" * 32, "", tuple(i.item_id for i in selected))
            output_tokens = max(1, decision.segment_tokens - budget.count([wrapper.message]))
            # Background is optional and deliberately omitted to maximize source capacity; old segments are immutable.
            try:
                if feedback:
                    text = summarizer.repair(selected, max_tokens=output_tokens, background=(), error=feedback)
                    feedback = ""
                else:
                    text = summarizer(selected, max_tokens=output_tokens, background=())
                validate_summary(text, selected)
                segment = CompactionSummary(uuid4().hex, text, tuple(i.item_id for i in selected))
                if budget.count([segment.message]) > decision.segment_tokens:
                    raise PreparationWait("COMPACTION_INVALID_OUTPUT", "摘要正文及来源包装超过新段预算。")
            except PreparationWait as exc:
                if (
                    exc.code == "COMPACTION_INVALID_OUTPUT"
                    and not repaired
                    and calls < limit
                    and callable(getattr(summarizer, "repair", None))
                ):
                    repaired = True
                    feedback = str(exc)
                    # Reuse the original sources, never recursively summarize the rejected output.
                    continue
                raise
            covered = set(segment.covered_sources)
            remaining = tuple(i for i in current.items if i.item_id not in covered)
            latest_state = current_state_item(current)
            candidate = replace(
                current,
                epoch_id=uuid4().hex,
                reason="compaction",
                state_digest=latest_state.source_digest,
                summaries=current.summaries + (segment,),
                items=(latest_state,) + tuple(i for i in remaining if i.item_id != latest_state.item_id),
            )
            validate_successor(current, candidate)
            _require_state(candidate, required_state)
            if budget.count(candidate.messages) >= budget.count(current.messages):
                raise PreparationWait("COMPACTION_NO_GAIN", "摘要未减少完整上下文大小。")
            current = candidate
    except PreparationWait as exc:
        if current is not epoch:
            exc.candidate_epoch = current
        raise
    except Exception as exc:
        wait = PreparationWait("COMPACTION_GENERATION_FAILED", f"摘要调用失败：{exc}")
        if current is not epoch:
            wait.candidate_epoch = current
        raise wait from exc
