# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Bind assistant edit requests to the captured rows and the workbench commands."""

from dataclasses import dataclass

from transbridge.application.translation.entry_alias import ai_entry_key
from transbridge.application.workbench_edits import (
    EntryEdit,
    changed_entry_states,
    stage_edit,
    translation_edit,
    validate_stage,
)

from ._project_tool_mutations import ProjectToolTarget
from .diagnostics import technical_error
from .types import ToolResult
from .undo_capture import capture_variant_command
from .workbench_rows import current_workbench_source


@dataclass(frozen=True, slots=True)
class PreparedEntryEdits:
    edits: tuple[EntryEdit, ...]
    missing: tuple[str, ...]


def prepare_entry_edits(tool, args, ctx, collection, *, projected=None) -> PreparedEntryEdits:
    captured = getattr(ctx, "_target_collection", None)
    source = captured if captured is not None else collection
    if source is None:
        raise ValueError("当前没有加载翻译集合")
    if tool == "edit_translation":
        ids = (args["entry_id"],)
    elif tool == "set_stage":
        validate_stage(args["stage"])
        ids = args["entry_ids"]
        if not ids:
            raise ValueError("entry_ids 不能为空")
    else:
        raise ValueError(f"未知的编辑工具：{tool}")
    edits = {}
    missing = []
    selected = getattr(ctx, "selected_entry_ids", ())
    for entry_id in ids:
        if not isinstance(entry_id, str) or not entry_id:
            raise ValueError("条目标识必须为非空字符串。")
        entry = source.get(entry_id, reject_ambiguous=True)
        if entry is None:
            if tool == "edit_translation":
                raise ValueError(f"条目不存在: {entry_id}")
            if entry_id not in missing:
                missing.append(entry_id)
            continue
        if selected and ai_entry_key(entry) not in selected and entry.identity.serialize() not in selected:
            raise ValueError(f"词条不在已确认的选区内：{entry_id}")
        if entry.identity in edits:
            continue
        before = projected.get(entry.identity) if projected else None
        if before is None:
            before = entry.snapshot()
        edits[entry.identity] = (
            translation_edit(before, args["new_translation"], args.get("new_stage"))
            if tool == "edit_translation"
            else stage_edit(before, args["stage"])
        )
    return PreparedEntryEdits(tuple(edits.values()), tuple(missing))


def _commit_edits(tool, ctx, prepared, warnings):
    target = ProjectToolTarget.capture(ctx)
    app = target.app

    def commit():
        expected = target.expected()
        current = current_workbench_source(ctx, app.collection)
        projection = app.entry_projection(edit.before.entry_key for edit in prepared.edits)
        for edit in prepared.edits:
            key = edit.before.entry_key
            if target.collection is None or target.collection.get(key) is None:
                raise ValueError("词条不在捕获的来源或选区内。")
            entry = None if current is None else current.get(key)
            if not projection.matches(edit.before, entry):
                raise ValueError("目标词条已被其他操作修改，请重新载入后核对。")
        states = changed_entry_states(prepared.edits)
        if not states:
            return
        if app.project_commands is None or app.runtime_context is None:
            raise ValueError("工程写入服务不可用，请重新打开工程后再试。")

        def command():
            if tool == "edit_translation":
                (edit,) = prepared.edits
                return app.update_projected_entry(
                    edit.before.entry_key, translation=edit.translation, stage=edit.stage, **expected
                )
            return app.project_commands.replace_entry_states(states, app.runtime_context, **expected)

        result = capture_variant_command(ctx, command, warnings=warnings)
        if not result.is_success:
            raise ValueError("；".join(item.message for item in result.diagnostics) or "词条修改未提交。")
        target.acknowledge_projection(preserve_scope=True)

    target.dispatch(commit)


def execute_entry_edit(tool, args, ctx, collection):
    warnings = []
    try:
        review = getattr(ctx, "entry_edit_review", None)
        prepared = (
            review.resolve(tool, args, ctx) if review is not None else prepare_entry_edits(tool, args, ctx, collection)
        )
        _commit_edits(tool, ctx, prepared, warnings)
    except (RuntimeError, TypeError, ValueError, OSError) as exc:
        return ToolResult.fail(technical_error(exc))
    if tool == "edit_translation":
        (edit,) = prepared.edits
        return ToolResult.ok(
            f"已更新 {args['entry_id']}" if edit.changed else f"{args['entry_id']} 的译文和状态已一致",
            data={
                "entry_id": args["entry_id"],
                "old_translation": edit.before.translation[:100],
                "new_translation": edit.translation[:100],
                "stage": edit.stage,
                "stage_changed": edit.stage != edit.before.stage,
            },
            warnings=warnings or None,
        )
    updated = sum(edit.changed for edit in prepared.edits)
    message = f"已将 {updated} 条条目设为 stage={args['stage']}"
    if prepared.missing:
        result = ToolResult.partial_ok(
            f"{message}（{len(prepared.missing)} 条未找到）",
            data={"updated_count": updated, "not_found": list(prepared.missing)},
            failed_items=[{"entry_id": key, "reason": "条目不存在"} for key in prepared.missing],
        )
        result.warnings = warnings or None
        return result
    return ToolResult.ok(message, data={"updated_count": updated}, warnings=warnings or None)
