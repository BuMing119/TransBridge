# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""工具系统基础设施：GuardChain, 装饰器, 执行函数。

装饰器推荐顺序：
    @require_collection    ← 最外层（集合前置检查，失败直接返回）
    @validate_params(...)   ← 内层（参数校验，失败直接返回）

示例：
    @require_collection
    @validate_params({"entry_id": {"type": "str", "required": True}})
    def _tool_edit_translation(args: dict, ctx, collection) -> ToolResult:
        ...
"""

from __future__ import annotations

from collections.abc import Callable
from copy import copy
import functools
import logging

from transbridge.application.workbench_filters import filter_entries as filter_entries

from .types import (
    ExecutionContext,
    HITLRequest as HITLRequest,
    HITLResponse as HITLResponse,
    HITLType as HITLType,
    ToolResult,
)

logger = logging.getLogger(__name__)

# ── execute_with_guardrails (B6) ────────────────────────────────


def _build_guard_chain() -> list | None:
    """构建标准护栏中间件链。B1: 消除 GUI/MCP 双重路径分歧。

    ExecutionEngine 复用此函数，确保所有工具执行路径使用相同的护栏链。
    返回 None 表示护栏模块不可用。
    """
    try:
        from transbridge.smart_assistant.guardrails.input_validator import InputValidationGuard
        from transbridge.smart_assistant.guardrails.output_validator import OutputValidationGuard
        from transbridge.smart_assistant.guardrails.permission import PermissionGuard

        return [PermissionGuard(), InputValidationGuard(), OutputValidationGuard()]
    except ImportError:
        return None


def _apply_after_guards(guards: list, step: dict, tool_name: str, success: bool, message: str, data: dict | None, ctx):
    """Apply after-execution guard middleware chain (onion model, reverse order).

    Returns (StepResult, rejection_or_None).
    Extracted from execute_with_guardrails to deduplicate ToolResult/dict branches (m5).
    """
    from transbridge.smart_assistant.execution_engine import StepResult

    temp = StepResult(
        step_id="",
        tool=tool_name,
        success=success,
        message=message,
        data=data,
        duration_ms=0,
    )
    for mw in reversed(guards):
        gr = mw.after_execute(step, temp, ctx)
        if not gr.allowed:
            return temp, gr
        if gr.modified_result is not None:
            temp.data = gr.modified_result
    return temp, None


def _guard_failure(result, *, phase: str) -> ToolResult:
    """Preserve guard diagnostics so a safe retry can repair invalid arguments."""
    code = str(result.code or "GUARD_REJECTED").upper()
    if code == "CAPABILITY_UNAVAILABLE":
        category = "config"
    elif code.startswith(("CONFIRMATION_", "PATH_")):
        category = "permission"
    elif phase == "input":
        category = "input"
    else:
        category = "internal"
    details = {"phase": phase}
    if result.json_pointer:
        details["json_pointer"] = result.json_pointer
    if result.validation_issues:
        details["validation_issues"] = result.validation_issues
    return ToolResult.fail(
        f"{'护栏拒绝' if phase == 'input' else '输出校验拒绝'}: {result.reason}",
        data=details,
        error_category=category,
        error_code=code,
        recovery_action="adjust_arguments" if category == "input" else None,
    )


def execute_with_guardrails(
    spec,
    args: dict,
    ctx: ExecutionContext,
    middlewares: list | None = None,
    *,
    tool_name: str | None = None,
) -> ToolResult:
    """统一工具执行入口，GUI 和 MCP 共享同一条中间件链。

    链: PermissionGuard → InputValidationGuard → 工具执行 → OutputValidationGuard
    B6+B1: 消除 GUI/MCP 安全分叉，支持自定义护栏链。
    middlewares=None 时使用默认链；传入 [] 则跳过所有护栏。
    """
    if getattr(ctx, "assistant_gate", None) is not None and not getattr(ctx, "assistant_step_managed", False):
        from transbridge.smart_assistant.step_execution import run_step

        return run_step(
            {"tool": tool_name or spec.name, "args": args},
            ctx,
            lambda current_args, context: execute_with_guardrails(
                spec, current_args, context, middlewares, tool_name=tool_name
            ),
        )
    guards = middlewares if middlewares is not None else _build_guard_chain()
    if guards is None:
        logger.critical("护栏模块导入失败，拒绝执行工具: %s", spec.name)
        return ToolResult.fail("安全护栏不可用，工具执行被拒绝")

    resolved_tool_name = tool_name or getattr(spec, "name", "")
    step = {"tool": resolved_tool_name, "args": args}

    # 1. Before 中间件链
    for mw in guards:
        guard_result = mw.before_execute(step, ctx)
        if not guard_result.allowed:
            return _guard_failure(guard_result, phase="input")
        if getattr(guard_result, "code", "") == "CONFIRMED":
            from .terminology_approval import REVIEW_ACTIONS, record_review

            if resolved_tool_name in REVIEW_ACTIONS:
                ctx = copy(ctx)
                record_review(ctx, step)
        if guard_result.modified_args is not None:
            step["args"] = guard_result.modified_args

    # Request admission wraps the same execution path used by nested plans.
    gate = getattr(ctx, "assistant_gate", None)
    handle = None
    if gate is not None:
        from transbridge.application.assistant_requests.models import RequestError

        try:
            ctx = copy(ctx)
            handle = gate.before(resolved_tool_name, step.get("args", args), ctx)
            ctx.assistant_effect_id = handle.effect_id
        except RequestError as error:
            return ToolResult.fail(str(error), error_category="permission", error_code=error.code)
    elif getattr(ctx, "assistant_effect_id", "") or getattr(ctx, "assistant_required", False):
        return ToolResult.fail("请求执行许可缺失", error_category="permission", error_code="TURN_LEASE_STALE")
    # 2. 执行
    try:
        raw_result = spec.execute(step.get("args", args), ctx)
    except Exception as error:
        if gate is not None and handle is not None:
            gate.failed(handle, error)
        raise
    if gate is not None and handle is not None:
        outcome = (
            raw_result
            if isinstance(raw_result, ToolResult)
            else ToolResult(
                success=bool(raw_result.get("success", False)),
                message=str(raw_result.get("message", "")),
                data=raw_result.get("data"),
            )
            if isinstance(raw_result, dict)
            else ToolResult.fail("工具未返回有效结果")
        )
        gate.after(handle, outcome)

    # 3. After 中间件链（逆序 — 洋葱模型） — m5: 提取 _apply_after_guards 消除重复
    if isinstance(raw_result, ToolResult):
        step_result, rejection = _apply_after_guards(
            guards, step, resolved_tool_name, raw_result.success, raw_result.message, raw_result.data, ctx
        )
        if rejection:
            return _guard_failure(rejection, phase="output")
        raw_result.message = step_result.message
        if step_result.data is not raw_result.data:
            raw_result.data = step_result.data
        from transbridge.application.security import project_business_data

        return project_business_data(raw_result)
    elif isinstance(raw_result, dict):
        result = ToolResult(
            success=raw_result.get("success", True),
            message=raw_result.get("message", ""),
            data=raw_result.get("data"),
        )
        step_result, rejection = _apply_after_guards(
            guards, step, resolved_tool_name, result.success, result.message, result.data, ctx
        )
        if rejection:
            return _guard_failure(rejection, phase="output")
        result.message = step_result.message
        if step_result.data is not result.data:
            result.data = step_result.data
        from transbridge.application.security import project_business_data

        return project_business_data(result)
    return raw_result


# ── @require_collection ─────────────────────────────────────────


def require_collection(func: Callable) -> Callable:
    """装饰器：从 ctx 提取 collection 并检查有效性。

    装饰后的函数签名: func(args: dict, ctx, collection: TranslationEntryCollection) -> ToolResult
    失败时直接返回 ToolResult(success=False)，不进入函数体。
    """

    @functools.wraps(func)
    def wrapper(args: dict, ctx) -> ToolResult:
        slot = getattr(ctx, "active_slot", None)
        collection = getattr(slot, "collection", None) if slot else getattr(ctx, "collection", None)
        if not collection or len(collection) == 0:
            return ToolResult.fail("当前没有加载翻译集合", error_category="input", error_code="COLLECTION_NOT_LOADED")
        return func(args, ctx, collection)

    return wrapper


def require_runtime_context(func: Callable) -> Callable:
    """Reject missing injected context before any controller or side effect runs.

    ``config`` is the legacy ToolResult category mapped by the S02 compatibility
    adapter to the canonical ``prerequisite`` category.
    """

    @functools.wraps(func)
    def wrapper(args: dict, ctx, *rest) -> ToolResult:
        app_context = getattr(ctx, "app_context", object()) if ctx is not None else None
        if ctx is None or app_context is None:
            return ToolResult.fail(
                "运行上下文未初始化",
                error_category="config",
                error_code="RUNTIME_CONTEXT_REQUIRED",
                recovery_action="inject_runtime_context",
            )
        return func(args, ctx, *rest)

    return wrapper


# ── @validate_params ────────────────────────────────────────────


def validate_params(schema: dict) -> Callable:
    """装饰器工厂：按 ToolSpec.parameters 格式校验 args。

    schema 格式: {"param_name": {"type": "str", "required": True, "description": "..."}}
    校验失败时返回 ToolResult(success=False)，不进入函数体。
    支持类型: str, int, float, bool, list, dict
    """

    from transbridge.application.tools.schema import (
        LegacySchemaConversionError,
        ToolSchemaError,
        canonicalize_parameters,
        validate_arguments,
    )

    try:
        canonical_schema = canonicalize_parameters(schema)
        schema_error = ""
    except (LegacySchemaConversionError, ToolSchemaError) as exc:
        canonical_schema = canonicalize_parameters({})
        schema_error = str(exc)

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(args: dict, *rest) -> ToolResult:
            if schema_error:
                return ToolResult.fail(
                    f"工具参数能力不可用: {schema_error}",
                    error_category="config",
                    error_code="CAPABILITY_UNAVAILABLE",
                )
            errors = validate_arguments(canonical_schema, args)
            if errors:
                error = errors[0]
                return ToolResult.fail(
                    f"参数校验失败 {error.pointer}: {error.message}",
                    data={
                        "phase": "input",
                        "json_pointer": error.pointer,
                        "validation_issues": [issue.to_dict() for issue in errors],
                    },
                    error_category="input",
                    error_code="ARGUMENT_SCHEMA_INVALID",
                    recovery_action="adjust_arguments",
                )

            return func(args, *rest)

        return wrapper

    return decorator
