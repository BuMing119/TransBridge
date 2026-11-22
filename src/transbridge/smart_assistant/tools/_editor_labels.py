# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Adapt assistant label requests to the workbench label and projection commands."""

from copy import deepcopy

from transbridge.application.translation.entry_alias import ai_entry_key
from transbridge.application.workbench_labels import LabelAliases, WorkbenchLabels

from .diagnostics import technical_error
from .types import ExecutionContext, ToolResult
from .workbench_rows import filtered_tool_rows, label_source_identity


def resolve_label_id(library, name):
    matches = [key for key, value in library.items() if value.get("name") == name]
    if not matches:
        raise ValueError(f"标签不存在：{name}")
    if len(matches) > 1:
        raise ValueError(f"有多个标签名为“{name}”，请在工作台区分名称后重试。")
    return matches[0]


def capture_label_scope(args, ctx):
    """Pin the displayed source and filter before a permission dialog processes events."""
    object.__setattr__(ctx, "collection", ctx._target_collection)
    if args.get("action") == "batch_assign":
        object.__setattr__(ctx, "filter_state", deepcopy(ctx.filter_state))


def batch_label_targets(ctx, collection, filter_state, entry_labels, label_id):
    aliases = LabelAliases()
    rows = filtered_tool_rows(ctx, collection, filter_state, entry_labels, observe=aliases.observe)
    total = 0
    changes = []
    targets = set()
    for row in rows:
        total += 1
        targets.add(row[1])
        if WorkbenchLabels.would_change(row[2], label_id, True):
            changes.append(row)
    aliases.require_unique(targets)
    return total, changes


def _explicit_rows(ctx, collection, entry_labels, entry_ids):
    requested = set(entry_ids)
    if not requested:
        raise ValueError("请提供 entry_ids")
    matches = {}
    aliases = LabelAliases()
    for entry, key, labels in filtered_tool_rows(ctx, collection, {}, entry_labels, observe=aliases.observe):
        ids = {ai_entry_key(entry), key, entry.identity.serialize()}
        if entry.requires_original_match and requested.intersection((entry.id, entry.key)):
            raise ValueError("词条 ID 对应多条原文，请使用包含原文的完整 EntryKey 选择条目。")
        for alias in requested.intersection(ids):
            if alias in matches:
                raise ValueError("条目标识不唯一，请使用完整 EntryKey。")
            matches[alias] = (entry, key, labels)
    if requested - matches.keys():
        raise ValueError("部分词条不属于当前来源或选区，请重新选择。")
    aliases.require_unique(key for _, key, _ in matches.values())
    return matches.values()


def manage_entry_labels(args, ctx, collection):
    action = args["action"].strip().lower()
    if action not in ("create", "assign", "unassign", "batch_assign"):
        return ToolResult.fail(f"无效的 action: {action}，可选: create/assign/unassign/batch_assign")
    name = args.get("name", "").strip()
    if not name:
        return ToolResult.fail("标签名不能为空")
    version = getattr(ctx, "variant_revision", None)
    app = getattr(ctx, "app_context", None) or ctx
    expected_collection = getattr(app, "collection", None)
    before = WorkbenchLabels.copies(getattr(ctx, "entry_labels", {}) or {}, getattr(ctx, "label_library", {}) or {})
    member_labels, library = before
    scope = getattr(ctx, "_source_input_full_collection", None)
    source = scope if scope is not None else collection
    if expected_collection is not source:
        expected_collection = None
    source_identity = label_source_identity(
        getattr(ctx, "_target_source_key", getattr(ctx, "active_key", None)), source
    )
    warnings = []
    execution = ctx if callable(getattr(ctx, "commit_label_state", None)) else ExecutionContext(app_context=ctx)

    def commit(labels, definitions, exact_changes):
        execution.commit_label_state(
            labels,
            definitions,
            before=before,
            source_identity=source_identity,
            warnings=warnings,
            expected_variant_revision=version if action != "create" else None,
            exact_changes=exact_changes,
            expected_collection=expected_collection,
        )
        return True

    workbench = WorkbenchLabels(commit)
    try:
        if isinstance(ctx, ExecutionContext) and ctx._target_source_key != getattr(app, "active_key", None):
            raise ValueError("活动来源已变化，请重新核对标签范围。")
        if action == "create":
            color = args.get("color", "#409EFF")
            _, updated = workbench.create_many(member_labels, library, {}, name, (color,))
            (label_id,) = updated.keys() - library.keys()
            return ToolResult.ok(
                f"已创建标签: {name}",
                data={"label_id": label_id, "name": name, "color": color},
                warnings=warnings or None,
            )
        if collection is None:
            return ToolResult.fail("需要活跃集合，请先加载翻译数据")
        lid = resolve_label_id(library, name)
        checked = action != "unassign"
        if action == "batch_assign":
            total, rows = batch_label_targets(ctx, collection, ctx.filter_state, member_labels, lid)
        else:
            rows = [
                row
                for row in _explicit_rows(ctx, collection, member_labels, args.get("entry_ids", ()))
                if workbench.would_change(row[2], lid, checked)
            ]
        prepared = dict(member_labels)
        targets = {}
        for entry, key, existing in rows:
            prepared[key] = existing
            targets[key] = entry.identity
        workbench.toggle_many(prepared, library, targets, lid, checked)
        count = len(targets)
        data = {"assigned_count" if checked else "removed_count": count}
        if action == "batch_assign":
            data["filter_total"] = total
        verb = "添加" if checked else "移除"
        return ToolResult.ok(f"已为 {count} 条词条{verb}标签 '{name}'", data=data, warnings=warnings or None)
    except Exception as exc:
        return ToolResult.fail(f"标签操作失败：{technical_error(exc)}")
