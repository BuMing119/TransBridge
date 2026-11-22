# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Bind the desktop dictionary command to an admitted assistant request."""

from transbridge.application.projects.dictionary_apply import dictionary_mod_id, prepare_dictionary
from transbridge.translation_memory.manager import TranslationMemoryManager

from ._project_tool_mutations import ProjectToolTarget
from .diagnostics import technical_error
from .types import ExecutionContext, ToolResult
from .undo_capture import capture_variant_command
from .workbench_rows import check_workbench_source


def apply_dictionary(ctx) -> ToolResult:
    execution = ctx if isinstance(ctx, ExecutionContext) else ExecutionContext(app_context=ctx)
    target = ProjectToolTarget.capture(execution)
    app = target.app
    if target.collection is None:
        return ToolResult.fail("当前没有加载翻译集合", error_category="input", error_code="COLLECTION_NOT_LOADED")
    commands = getattr(app, "project_commands", None)
    caller = execution.request_context or getattr(app, "runtime_context", None)
    if None in (caller, target.identity, target.project_revision, target.variant_revision) or commands is None:
        return ToolResult.fail("工程写入服务不可用，词典尚未套用。", error_category="config")

    mod_id = execution._target_dictionary_mod_id
    token = getattr(execution, "cancellation_token", None)

    def check_input():
        if token is not None:
            token.raise_if_cancelled()
        check_workbench_source(execution, app)
        if dictionary_mod_id(app) != mod_id:
            raise ValueError("词典匹配的来源文件已变化，请重新核对。")
        if (target.project_revision, target.variant_revision) != (app.project_revision, app.variant_revision):
            raise ValueError("工程或译文已变化，请重新套用词典。")

    check_input()
    expected = target.expected()
    plan = prepare_dictionary(target.collection, TranslationMemoryManager(), mod_file_id=mod_id)
    if plan.choices:
        reviewer = getattr(app, "review_dictionary_conflicts", None)
        if not callable(reviewer):
            return ToolResult.fail("词典有多个候选译文，请在桌面工作台处理冲突。本次未写入。", error_category="config")

        def review():
            check_input()
            return reviewer(plan.choices)

        # Let the modal dialog wait on Qt without holding the request commit lock.
        selections = ExecutionContext(app_context=app).safe_mutate_wait(review)
        plan.apply_choices(selections)

    warnings = []
    committed = False

    def commit():
        nonlocal committed
        check_input()
        if not plan.states:
            return
        result = capture_variant_command(
            execution, lambda: plan.commit(commands, caller, **expected), warnings=warnings
        )
        if not result.is_success:
            raise ValueError("；".join(item.message for item in result.diagnostics) or "词典套用未提交。")
        committed = True
        target.acknowledge_projection(preserve_scope=True)

    try:
        execution.safe_mutate_wait(commit)
    except Exception as exc:
        if not committed:
            raise
        warnings.append(f"词典已套用，但助手未能确认提交回执：{technical_error(exc)}。请勿重复执行。")
    stats = plan.result
    return ToolResult.ok(
        f"词典已填充 {stats.applied} 条译文，{len(stats.needs_review)} 条原文有变化，需复核。",
        data={
            "applied": stats.applied,
            "key_hits": stats.key_hits,
            "text_hits": stats.text_hits,
            "misses": stats.misses,
            "needs_review": len(stats.needs_review),
            "conflicts": plan.conflict_count,
        },
        warnings=warnings or None,
    )
