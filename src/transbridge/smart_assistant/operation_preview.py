# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Read-only proposed change projection using the same admitted request scope."""

from __future__ import annotations

from dataclasses import dataclass

from transbridge.application.translation.entry_alias import ai_entry_key
from transbridge.application.workbench_filters import updated_filter_state
from transbridge.converter.translation_entry import STAGE_LABELS

from .entry_edit_review import EDIT_TOOLS, EntryEditReview, prepare_reviews
from .request_scope import WORKBENCH_SCOPE_TOOLS
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
    edit_review: EntryEditReview | None = None


def bind_entry_edit_review(step, context, review_for):
    """Use the approved card, or capture automatic ReAct's targets before its permission dialog."""
    if step.get("tool") not in EDIT_TOOLS:
        return
    review = review_for(step)
    if review is None:
        (preview,) = build_operation_previews([step], context)
        if preview.edit_review is None:
            raise ValueError(preview.notice)
        review = preview.edit_review
    context.entry_edit_review = review


def _targets(tool, args, context, collection, scope):
    if tool in {"start_translation", "start_polish"}:
        from .tools.translation_scope import translation_targets

        mode = "polish" if tool == "start_polish" else args.get("mode", "translate")
        entries = translation_targets(args, context, collection, mode=mode, scope_override=scope)
        return [entry.identity.serialize() for entry in entries]
    if "entry_id" in args:
        return [args["entry_id"]]
    if isinstance(args.get("entry_ids"), list):
        return args["entry_ids"]
    return None


def build_operation_previews(steps, context) -> tuple[OperationChangePreview, ...]:
    """Run off the GUI thread; this never prepares, approves, or executes effects."""
    gate = getattr(context, "assistant_gate", None)
    workbench = any(step.get("tool") in WORKBENCH_SCOPE_TOOLS for step in steps)
    if gate is not None:
        from .request_scope import apply_captured_scope

        request = gate.current_request()
        state = gate.service.state(gate.context)
        apply_captured_scope(request, state.get("ingress", ()), context, workbench=workbench)
    elif getattr(context, "assistant_required", False):
        raise ValueError("原任务范围不可用，请重新查看确认")
    collection = getattr(context, "_target_collection", None)
    if collection is None:
        collection = getattr(context, "collection", None)
    previews = []
    filters = getattr(context, "filter_state", {})
    scope = None
    reviews = prepare_reviews(steps, context, collection)
    for index, step in enumerate(steps):
        tool, args = str(step.get("tool", "")), step.get("args", {})
        if tool == "set_filters" and isinstance(args, dict):
            filters = updated_filter_state(filters, args, getattr(context, "label_library", {}))
        if tool == "set_scope" and isinstance(args, dict):
            from transbridge.application.translation.task_scope import TranslationScope

            TranslationScope.from_filters(args)
            scope = args
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
        if tool == "manage_entry_labels" and str(args.get("action", "")).strip().lower() == "batch_assign":
            from .tools._editor_labels import batch_label_targets, resolve_label_id

            name = args.get("name", "").strip()
            labels = getattr(context, "label_library", {})
            try:
                label_id = resolve_label_id(labels, name)
            except ValueError as exc:
                previews.append(OperationChangePreview(tool, notice=str(exc)))
                continue
            _, changed = batch_label_targets(
                context, collection, filters, getattr(context, "entry_labels", {}), label_id
            )
            rows = tuple(
                ProposedEntry(
                    ai_entry_key(entry), entry.original, entry.translation, entry.translation, f"添加标签：{name}"
                )
                for entry, _, _ in changed
            )
            previews.append(OperationChangePreview(tool, rows, "执行前快照；执行时仍会重新核验任务范围。", True))
            continue
        if tool in EDIT_TOOLS:
            review = reviews[index]
            if isinstance(review, str):
                rows = ()
                if tool == "edit_translation":
                    rows = (ProposedEntry(str(args.get("entry_id", "")), None, None, args.get("new_translation")),)
                previews.append(OperationChangePreview(tool, rows, review))
                continue
            rows = []
            for edit in review.prepared.edits:
                before = edit.before
                key = before.entry_key
                identity = key.serialize() if key.original is not None else key.local_key
                detail = f"词条状态：{STAGE_LABELS[before.stage]} → {STAGE_LABELS[edit.stage]}"
                rows.append(ProposedEntry(identity, before.original, before.translation, edit.translation, detail))
            rows.extend(ProposedEntry(key, None, None, None, "未找到唯一词条") for key in review.prepared.missing)
            previews.append(OperationChangePreview(tool, tuple(rows), "执行前会再次核对此处的词条值。", True, review))
            continue
        targets = _targets(tool, args, context, collection, scope)
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
            after = None
            detail = ""
            if tool in {"start_translation", "start_polish"}:
                detail = "执行时生成，确认前尚无拟译文"
            elif tool == "manage_entry_labels":
                action = "移除" if args.get("action") == "unassign" else "添加"
                detail = f"{action}标签：{args.get('name', '未指定')}；译文保持不变"
                after = entry.translation
            else:
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
        apply_captured_scope(request, state.get("ingress", ()), context, workbench=workbench)
    return tuple(previews)
