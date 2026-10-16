"""Read-only proposed change projection using the same admitted request scope."""

from __future__ import annotations

from dataclasses import dataclass

from transbridge.application.translation.entry_alias import ai_entry_key
from transbridge.converter.translation_entry import STAGE_LABELS

from .tool_registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class ProposedEntry:
    entry_id: str
    original: str | None
    before: str | None
    after: str | None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class OperationChangePreview:
    tool: str
    entries: tuple[ProposedEntry, ...] = ()
    notice: str = ""
    resolved: bool = False


def _targets(tool, args, context, collection):
    if tool == "manage_entry_labels" and args.get("action") == "batch_assign":
        from .tools.base import filter_entries

        return [
            ai_entry_key(entry)
            for entry in filter_entries(
                collection, context.filter_state, entry_labels=getattr(context, "entry_labels", None)
            )
        ]
    if tool == "run_postprocess":
        from .tools._postprocess_tool_runtime import _resolve_entries, _resolve_profile

        config, *_ = _resolve_profile(str(args.get("profile", "polish")))
        entries, _ = _resolve_entries(args, context, collection, config)
        return [ai_entry_key(entry) for entry in entries]
    if tool == "start_translation" and not args.get("entry_ids"):
        from .tools.base import filter_entries, resolve_scope_to_entry_ids

        return resolve_scope_to_entry_ids(context, collection) or [
            ai_entry_key(entry) for entry in filter_entries(collection, {"stage": [0]})
        ]
    if tool == "start_polish" and args.get("entry_ids") is None:
        scope = args.get("scope", "all")
        if scope not in {"all", "passed", "has_issues"}:
            raise ValueError("润色范围无效")
        return [
            ai_entry_key(entry)
            for entry in collection
            if entry.translation
            and (
                scope == "all"
                or scope == "passed"
                and entry.stage in (1, 3, 4, 5, 6)
                or scope == "has_issues"
                and entry.stage == 2
            )
        ]
    if "entry_id" in args:
        return [args["entry_id"]]
    if isinstance(args.get("entry_ids"), list):
        return args["entry_ids"]
    return None


def build_operation_previews(steps, context) -> tuple[OperationChangePreview, ...]:
    """Run off the GUI thread; this never prepares, approves, or executes effects."""
    gate = getattr(context, "assistant_gate", None)
    if gate is not None:
        from .request_scope import apply_captured_scope

        request = gate.current_request()
        state = gate.service.state(gate.context)
        apply_captured_scope(request, state.get("ingress", ()), context)
    elif getattr(context, "assistant_required", False):
        raise ValueError("原任务范围不可用，请重新查看确认")
    collection = getattr(context, "_target_collection", None)
    if collection is None:
        collection = getattr(context, "collection", None)
    previews = []
    for step in steps:
        tool, args = str(step.get("tool", "")), step.get("args", {})
        spec = ToolRegistry.get(tool)
        if spec is not None and spec.permission == "read":
            previews.append(OperationChangePreview(tool, notice="读取信息，不修改词条译文。"))
            continue
        if not isinstance(args, dict):
            previews.append(OperationChangePreview(tool, notice="参数无效，无法核对修改范围。"))
            continue
        if collection is None:
            previews.append(OperationChangePreview(tool, notice="没有可核验的原词条；当前译文和原文未知。"))
            continue
        targets = _targets(tool, args, context, collection)
        if targets is None:
            previews.append(OperationChangePreview(tool, notice="此操作没有可预览的词条修改；操作参数见高级详情。"))
            continue
        rows = []
        for identity in dict.fromkeys(str(identity) for identity in targets):
            entry = collection.get(identity)
            if entry is None:
                rows.append(
                    ProposedEntry(identity, None, None, args.get("new_translation"), "未找到唯一词条，无法核验旧值")
                )
                continue
            after = args.get("new_translation") if tool == "edit_translation" else None
            detail = ""
            if tool in {"start_translation", "start_polish", "run_postprocess"}:
                detail = "执行时生成，确认前尚无拟译文"
            elif "stage" in args or "new_stage" in args:
                stage = args.get("new_stage", args.get("stage"))
                old_label = STAGE_LABELS.get(entry.stage, str(entry.stage))
                detail = f"词条状态：{old_label} → {STAGE_LABELS.get(stage, str(stage))}"
                if tool != "edit_translation":
                    after = entry.translation
            elif tool == "manage_entry_labels":
                action = "移除" if args.get("action") == "unassign" else "添加"
                detail = f"{action}标签：{args.get('name', '未指定')}；译文保持不变"
                after = entry.translation
            elif tool != "edit_translation":
                detail = "其他属性操作；请核对操作摘要"
                after = entry.translation
            rows.append(
                ProposedEntry(ai_entry_key(entry), getattr(entry, "original", None), entry.translation, after, detail)
            )
        notice = "执行前快照；执行时仍会重新核验任务范围。"
        if len(steps) > 1:
            notice += "前序步骤可能改变后续步骤的输入。"
        previews.append(OperationChangePreview(tool, tuple(rows), notice, True))
    # A view/project switch during the read must not turn another version into this proposal's preview.
    if gate is not None:
        apply_captured_scope(request, state.get("ingress", ()), context)
    return tuple(previews)
