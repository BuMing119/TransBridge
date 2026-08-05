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

import functools
import logging
from typing import Callable, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection
    from src.transbridge.converter.translation_entry import TranslationEntry

logger = logging.getLogger(__name__)

from .types import (
    ToolResult, ExecutionContext, HITLType, HITLRequest, HITLResponse,
)


# ── execute_with_guardrails (B6) ────────────────────────────────

def _build_guard_chain() -> list | None:
    """构建标准护栏中间件链。B1: 消除 GUI/MCP 双重路径分歧。

    ExecutionEngine 复用此函数，确保所有工具执行路径使用相同的护栏链。
    返回 None 表示护栏模块不可用。
    """
    try:
        from src.transbridge.smart_assistant.guardrails.permission import PermissionGuard
        from src.transbridge.smart_assistant.guardrails.input_validator import InputValidationGuard
        from src.transbridge.smart_assistant.guardrails.output_validator import OutputValidationGuard
        return [PermissionGuard(), InputValidationGuard(), OutputValidationGuard()]
    except ImportError:
        return None


def _apply_after_guards(guards: list, step: dict, tool_name: str, success: bool,
                        message: str, data: dict | None, ctx):
    """Apply after-execution guard middleware chain (onion model, reverse order).

    Returns (StepResult, rejection_reason_or_None).
    Extracted from execute_with_guardrails to deduplicate ToolResult/dict branches (m5).
    """
    from src.transbridge.smart_assistant.execution_engine import StepResult
    temp = StepResult(
        step_id="", tool=tool_name, success=success,
        message=message, data=data, duration_ms=0,
    )
    for mw in reversed(guards):
        gr = mw.after_execute(step, temp, ctx)
        if not gr.allowed:
            return temp, gr.reason
        if gr.modified_result is not None:
            temp.data = gr.modified_result
    return temp, None


def execute_with_guardrails(spec, args: dict, ctx: ExecutionContext,
                            middlewares: list | None = None) -> ToolResult:
    """统一工具执行入口，GUI 和 MCP 共享同一条中间件链。

    链: PermissionGuard → InputValidationGuard → 工具执行 → OutputValidationGuard
    B6+B1: 消除 GUI/MCP 安全分叉，支持自定义护栏链。
    middlewares=None 时使用默认链；传入 [] 则跳过所有护栏。
    """
    guards = middlewares if middlewares is not None else _build_guard_chain()
    if guards is None:
        logger.critical("护栏模块导入失败，拒绝执行工具: %s", spec.name)
        return ToolResult.fail("安全护栏不可用，工具执行被拒绝")

    step = {"tool": spec.name, "args": args}

    # 1. Before 中间件链
    for mw in guards:
        guard_result = mw.before_execute(step, ctx)
        if not guard_result.allowed:
            return ToolResult.fail(f"护栏拒绝: {guard_result.reason}")
        if guard_result.modified_args is not None:
            step["args"] = guard_result.modified_args

    # 2. 执行
    raw_result = spec.execute(step.get("args", args), ctx)

    # 3. After 中间件链（逆序 — 洋葱模型） — m5: 提取 _apply_after_guards 消除重复
    if isinstance(raw_result, ToolResult):
        step_result, rejection = _apply_after_guards(
            guards, step, spec.name, raw_result.success,
            raw_result.message, raw_result.data, ctx)
        if rejection:
            return ToolResult.fail(f"输出校验拒绝: {rejection}")
        raw_result.message = step_result.message
        if step_result.data is not raw_result.data:
            raw_result.data = step_result.data
        return raw_result
    elif isinstance(raw_result, dict):
        result = ToolResult(
            success=raw_result.get("success", True),
            message=raw_result.get("message", ""),
            data=raw_result.get("data"),
        )
        step_result, rejection = _apply_after_guards(
            guards, step, spec.name, result.success,
            result.message, result.data, ctx)
        if rejection:
            return ToolResult.fail(f"输出校验拒绝: {rejection}")
        result.message = step_result.message
        if step_result.data is not result.data:
            result.data = step_result.data
        return result
    return raw_result


# ── filter_entries (H8) ────────────────────────────────────────

def filter_entries(collection: "TranslationEntryCollection",
                    filter_state: dict,
                    entry_labels: dict[str, set[str]] | None = None) -> list["TranslationEntry"]:
    """公共筛选函数。根据 filter_state 从 collection 中筛选条目。

    供 Story 04/08/10 复用，统一筛选行为。(H8)
    entry_labels: entry_id → set[label_name]，用于标签筛选（M1 修复）。
    """
    results = list(collection)

    stages = filter_state.get("stage")
    if stages:
        results = [e for e in results if e.stage in stages]

    categories = filter_state.get("category")
    if categories:
        results = [
            e for e in results
            if e.context and any(
                e.context.startswith(c + ":") or e.context.startswith(c + "_")
                for c in categories
            )
        ]

    labels = filter_state.get("labels") or filter_state.get("label")  # M1: 兼容两种 key 名
    if labels and entry_labels:
        results = [e for e in results if any(
            lbl in entry_labels.get(e.key, set()) for lbl in labels
        )]

    search_query = filter_state.get("search_query")
    search_field = filter_state.get("search_field", "original")
    if search_query:
        q = search_query.lower()
        if search_field == "id":
            results = [e for e in results if q in (e.id or "").lower()]
        elif search_field == "key":
            results = [e for e in results if q in (e.key or "").lower()]
        elif search_field in ("original", "text"):
            results = [e for e in results if q in (e.original or "").lower()]
        elif search_field == "translation":
            results = [e for e in results if q in (e.translation or "").lower()]
        elif search_field == "context":
            results = [e for e in results if q in (e.context or "").lower()]
        elif search_field == "all":
            results = [
                e for e in results
                if q in (e.key or "").lower()
                or q in (e.original or "").lower()
                or q in (e.translation or "").lower()
                or q in (e.context or "").lower()
            ]

    return results


# ── resolve_scope_to_entry_ids (M3) ────────────────────────────

def resolve_scope_to_entry_ids(ctx, collection: "TranslationEntryCollection") -> list[str] | None:
    """从 ctx.translation_scope 解析条目 ID 列表。

    供 start_translation / run_postprocess 等长运行工具复用，消除 scope 解析重复代码。

    Returns:
        条目 key 列表，若 scope 为空或无效则返回 None（调用方自行决定默认行为）。
    """
    scope = getattr(ctx, 'translation_scope', None)
    if not scope or not any(scope.get(k) for k in ('stages', 'labels', 'categories')):
        return None
    filter_state = {
        "stage": scope.get("stages"),
        "category": scope.get("categories"),
        "labels": scope.get("labels"),
    }
    entry_labels = getattr(ctx, 'entry_labels', None)
    scoped = filter_entries(collection, filter_state, entry_labels=entry_labels)
    return [e.key for e in scoped]


# ── @require_collection ─────────────────────────────────────────

def require_collection(func: Callable) -> Callable:
    """装饰器：从 ctx 提取 collection 并检查有效性。

    装饰后的函数签名: func(args: dict, ctx, collection: TranslationEntryCollection) -> ToolResult
    失败时直接返回 ToolResult(success=False)，不进入函数体。
    """
    @functools.wraps(func)
    def wrapper(args: dict, ctx) -> ToolResult:
        slot = getattr(ctx, 'active_slot', None)
        collection = getattr(slot, 'collection', None) if slot else getattr(ctx, 'collection', None)
        if not collection or len(collection) == 0:
            return ToolResult.fail("当前没有加载翻译集合",
                error_category="input", error_code="COLLECTION_NOT_LOADED")
        return func(args, ctx, collection)
    return wrapper


# ── _TYPE_MAP (M48: 移至模块级避免每次调用重建) ──────────────

_TYPE_MAP: dict[str, type] = {
    "str": str, "string": str,
    "int": int, "integer": int,
    "float": float, "number": float,
    "bool": bool, "boolean": bool,
    "list": list, "array": list,
    "dict": dict, "object": dict,
}


# ── @validate_params ────────────────────────────────────────────

def validate_params(schema: dict) -> Callable:
    """装饰器工厂：按 ToolSpec.parameters 格式校验 args。

    schema 格式: {"param_name": {"type": "str", "required": True, "description": "..."}}
    校验失败时返回 ToolResult(success=False)，不进入函数体。
    支持类型: str, int, float, bool, list, dict
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(args: dict, *rest) -> ToolResult:
            if not isinstance(args, dict):
                return ToolResult.fail(f"参数类型错误: 期望 dict，实际 {type(args).__name__}")
            errors = []
            for param_name, param_info in schema.items():
                expected_type_str = param_info.get("type", "str")
                expected_type = _TYPE_MAP.get(expected_type_str)
                required = param_info.get("required", True)

                if param_name not in args or args[param_name] is None:
                    if required:
                        errors.append(f"缺少必需参数: {param_name}")
                    continue

                value = args[param_name]
                if expected_type and not isinstance(value, expected_type):
                    errors.append(
                        f"参数类型错误: {param_name} 期望 {expected_type_str}，实际 {type(value).__name__}"
                    )

            if errors:
                return ToolResult.fail(f"参数校验失败: {'; '.join(errors)}")

            return func(args, *rest)
        return wrapper
    return decorator
